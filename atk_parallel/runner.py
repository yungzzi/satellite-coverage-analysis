# -*- coding: utf-8 -*-
"""并行调度器：TLE → 动态分片 → 多 headless ATK 实例并行覆盖 → 合并输出。

用法：
  python -m atk_parallel.runner --tle <三行或两行TLE> --t0 "12 Aug 2026 00:00:00.000" --t1 "12 Aug 2026 08:00:00.000" \
        --lat 30.25 --lon 120.17 --fov 45 --out-dir output

默认参数：每片 1000 颗、实例上限 min(核数, 可用内存/1.2GB, 12)、失败重试 2 次、
单片超时 1200s、CovMulti Assets 每批 250 条。输出 merged_windows.csv + summary.json。
"""
from __future__ import annotations
import argparse, ctypes, json, os, socket, subprocess, sys, tempfile, time
from datetime import datetime

ATK_ROOT = r"D:\ATK航天任务设计工作箱\ATK-v4.0.1-rc.5-windows-x64\ATK-4.0.1-rc.5"
ATK_EXE = os.path.join(ATK_ROOT, "ATK.exe")
HERE = os.path.dirname(os.path.abspath(__file__))

from .tle_utils import read_tle, validate_lines, to_two_line_file, make_shards, shard_norad_ranges


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def avail_memory_gb() -> float:
    try:
        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
        return st.ullAvailPhys / (1024 ** 3)
    except Exception:
        return 0.0


def decide_max_inst(requested: int | None) -> int:
    cores = os.cpu_count() or 4
    mem_gb = avail_memory_gb()
    by_mem = int(mem_gb / 1.2) if mem_gb > 0 else cores
    by_mem = max(1, by_mem)
    hard = min(cores, by_mem, 12)
    if requested:
        hard = min(requested, hard)
    return hard


def pick_free_ports(n: int, base: int = 6655, max_scan: int = 200) -> list[int]:
    """动态挑选 n 个当前空闲的本地端口（避免与其他软件已占用端口冲突）。

    用 bind 探测：能 bind 成功即空闲（ATK 启动后由 --port 监听同一端口）。
    """
    ports: list[int] = []
    p = base
    while len(ports) < n and p < base + max_scan:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            ports.append(p)
        except OSError:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
        p += 1
    return ports


def wait_port(port: int, timeout_s: float = 120) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        s = socket.socket(); s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port)); s.close(); return True
        except OSError:
            pass
        finally:
            s.close()
        time.sleep(0.5)
    return False


def launch_atk(port: int):
    # 隐藏 ATK 启动时弹出的使用协议/欢迎窗口（SW_HIDE）
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return subprocess.Popen(
        [ATK_EXE, "--headless", "--new-scenario", "--port", str(port),
         "--hide-startup", "--hide-welcome", "--no-crash-report"],
        cwd=ATK_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        startupinfo=si)


def start_shard_job(shard_i, port, shard_file, shard_recs, cfg, out_dir):
    """启动一片：ATK + worker，返回 job 字典（不等待）。"""
    atk_proc = launch_atk(port)
    ready = wait_port(port)
    out_path = os.path.abspath(os.path.join(out_dir, f"shard_{port}.json"))
    cpath = os.path.abspath(os.path.join(out_dir, f"shard_{port}_cfg.json"))
    wcfg = {
        "port": port, "tle": os.path.abspath(shard_file).replace("\\", "/"),
        "expected": len(shard_recs), "angles": None, "fov": cfg["fov"],
        "lat": cfg["lat"], "lon": cfg["lon"], "t0": cfg["t0"], "t1": cfg["t1"],
        "out": out_path,
    }
    with open(cpath, "w", encoding="utf-8") as f:
        json.dump(wcfg, f, ensure_ascii=False)
    w = subprocess.Popen([sys.executable, "-m", "atk_parallel.worker", "--config", cpath],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=os.path.dirname(HERE))
    return {"shard_i": shard_i, "port": port, "atk": atk_proc, "worker": w,
            "out": out_path, "ready": ready}


def collect_shard_job(job, cfg):
    """等待一片完成并校验，返回结果 dict 或 None（失败）。"""
    shard_i, port = job["shard_i"], job["port"]
    try:
        rc = job["worker"].wait(timeout=cfg.get("shard_timeout", 1200))
    except subprocess.TimeoutExpired:
        print(f"  [片 {shard_i}] port {port} worker 超时", flush=True)
        try: job["worker"].kill()
        except Exception: pass
        return None
    try: job["atk"].kill()
    except Exception: pass
    if rc != 0 or not os.path.exists(job["out"]):
        print(f"  [片 {shard_i}] port {port} worker 失败 rc={rc}", flush=True)
        return None
    try:
        with open(job["out"], encoding="utf-8") as f:
            res = json.load(f)
    except Exception:
        return None
    if not res.get("ok"):
        print(f"  [片 {shard_i}] port {port} 结果异常: {res.get('error')}", flush=True)
        return None
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tle", required=True, help="TLE 文件（三行或两行格式）")
    ap.add_argument("--t0", required=True, help='开始（"12 Aug 2026 00:00:00.000"）')
    ap.add_argument("--t1", required=True, help='结束（"12 Aug 2026 08:00:00.000"）')
    ap.add_argument("--lat", type=float, default=30.25)
    ap.add_argument("--lon", type=float, default=120.17)
    ap.add_argument("--fov", type=float, default=45.0, help="默认传感器半角（度）")
    ap.add_argument("--per-shard", type=int, default=1000, help="每片卫星数")
    ap.add_argument("--max-inst", type=int, default=None, help="并行实例上限（默认自动）")
    ap.add_argument("--retries", type=int, default=2, help="失败片重试次数")
    ap.add_argument("--shard-timeout", type=int, default=1200, help="单片超时（秒）")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--port-base", type=int, default=6655)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    recs = read_tle(args.tle)
    problems = validate_lines(recs)
    if problems:
        print(f"警告：{len(problems)} 条 TLE 行长度非 69（导入可能失败）", flush=True)
        for p in problems[:5]:
            print("  ", p, flush=True)
    print(f"卫星总数: {len(recs)}", flush=True)

    shards = make_shards(recs, args.per_shard)
    n_shards = len(shards)
    max_inst = decide_max_inst(args.max_inst)
    print(f"分片: {n_shards} 片（每片≤{args.per_shard}）| 并行上限: {max_inst} | 可用内存: {avail_memory_gb():.1f} GB", flush=True)

    # 分片 TLE 写系统 TEMP（ATK ImportTLEFile 不认中文路径）
    shard_dir = os.path.join(tempfile.gettempdir(), "atk_shards")
    os.makedirs(shard_dir, exist_ok=True)
    shard_files = []
    for i, sh in enumerate(shards):
        p = os.path.join(shard_dir, f"shard_{i}.tle")
        to_two_line_file(sh, p)
        shard_files.append(os.path.abspath(p))

    cfg = {**vars(args), "fov": args.fov}
    all_results = {}
    failed = []
    port_pool = pick_free_ports(max_inst, args.port_base)
    if len(port_pool) < max_inst:
        print(f"警告：只找到 {len(port_pool)} 个空闲端口（需要 {max_inst}）", flush=True)
        max_inst = len(port_pool)
        if max_inst == 0:
            print("错误：无空闲端口，终止", flush=True)
            sys.exit(1)

    t_wall = time.time()
    for round_no, start in enumerate(range(0, n_shards, max_inst)):
        batch = list(range(start, min(start + max_inst, n_shards)))
        print(f"== 第 {round_no + 1} 轮：片 {batch} ==", flush=True)
        pending = list(batch)
        for attempt in range(args.retries + 1):
            if not pending:
                break
            todo = list(pending)
            pending = []
            # 并行启动
            jobs = []
            for k, shard_i in enumerate(todo):
                port = port_pool[k]
                print(f"  [片 {shard_i}] port {port} 尝试 {attempt + 1}（{shard_norad_ranges(shards[shard_i])}）", flush=True)
                job = start_shard_job(shard_i, port, shard_files[shard_i], shards[shard_i], cfg, args.out_dir)
                if not job["ready"]:
                    print(f"  [片 {shard_i}] port {port} ATK 端口未就绪", flush=True)
                    try: job["atk"].kill()
                    except Exception: pass
                    pending.append(shard_i)
                    continue
                jobs.append(job)
            # 统一等待
            for job in jobs:
                res = collect_shard_job(job, cfg)
                if res is None:
                    pending.append(job["shard_i"])
                else:
                    all_results[job["shard_i"]] = res
                    print(f"  [片 {job['shard_i']}] 完成: {res['sats']}颗 {len(res['windows'])}窗口 {res['timing']}", flush=True)
            time.sleep(2)
        if pending:
            failed.extend(pending)
            for shard_i in pending:
                print(f"  [片 {shard_i}] 重试耗尽，标记失败（{shard_norad_ranges(shards[shard_i])}）", flush=True)
    wall = time.time() - t_wall

    # ---- 汇总 ----
    merged = []
    name_map = {}
    for shard_i, res in all_results.items():
        rec_list = shards[shard_i]
        for j, nm in enumerate(res.get("asset_order", [])):
            if j < len(rec_list):
                name_map[nm] = (rec_list[j].norad, rec_list[j].name)
        for w in res["windows"]:
            asset = w["asset"]
            base = asset[:-len("-S45")] if asset.endswith("-S45") else asset
            norad, name = name_map.get(base, ("?", asset))
            merged.append({
                "norad": norad, "name": name,
                "start_utc": w["start"], "end_utc": w["end"],
                "duration_s": w["duration_s"],
            })
    merged.sort(key=lambda r: r["start_utc"])

    csv_path = os.path.join(args.out_dir, "merged_windows.csv")
    import csv
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=["norad", "name", "start_utc", "end_utc", "duration_s"])
        wtr.writeheader()
        wtr.writerows(merged)

    summary = {
        "total_sats": len(recs),
        "shards": n_shards,
        "ok_shards": len(all_results),
        "failed_shards": failed,
        "failed_ranges": [shard_norad_ranges(shards[i]) for i in failed],
        "windows": len(merged),
        "wall_seconds": round(wall, 1),
        "output_csv": csv_path,
    }
    with open(os.path.join(args.out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print("=== 汇总 ===", flush=True)
    print(f"成功片 {len(all_results)}/{n_shards} | 窗口 {len(merged)} | 墙钟 {wall:.1f}s | 输出 {csv_path}", flush=True)
    if failed:
        print("缺失片（需补跑或人工核对）:", flush=True)
        for i, rng in zip(failed, summary["failed_ranges"]):
            print(f"  - 片 {i}: {rng}", flush=True)


if __name__ == "__main__":
    main()
