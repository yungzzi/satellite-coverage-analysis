# -*- coding: utf-8 -*-
"""逐星传感器参数表（Q140-Q143）回归自检。

覆盖点：
  1) 空表/无表 = 行为与全局默认完全一致；
  2) NORAD 精确匹配覆盖生效（视场半角 / 最小仰角 / 幅宽）；
  3) 未设置用全局默认；重复编号后写覆盖；无 norad 空行忽略；
  4) 幅宽换算：球面公式数值正确，且幅宽口径与同量级角度口径窗口量级一致；
  5) 收藏夹批量添加的 NORAD 提取（吉林一号全量 58 颗）；
  6) 汇总表/元信息补"实际所用参数"列；
  7) AppTest 首页 0 异常。

运行：.venv/Scripts/python src\validate_sensor_params.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coverage_analysis as cov
import satellite_loader as sl

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}{('  ' + detail) if detail else ''}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}{('  ' + detail) if detail else ''}")


def jilin_recs():
    cols = sl.load_builtin_collections()
    jl = next(c for c in cols if "吉林一号全量" in c["name"])
    return sl.resolve_collection(jl, offline=True)


def make_cfg():
    return {
        "region_name": "杭州5km",
        "region_mode": "circle",
        "tle_file": "自检（吉林一号测试快照）",
        "fov_half_angle_deg": 30.0,
        "min_elevation_deg": 10.0,
        "step_minutes": 1,
        "refine_step_seconds": 10,
        "grid_spacing_deg": 0.2,
        "target": {"lat": 30.25, "lon": 120.15, "radius_km": 5.0},
        "save_gantt": False,
        "save_plots": False,
        "max_gantt_rows": 15,
    }


def region():
    return {"mode": "circle", "lat": 30.25, "lon": 120.15, "radius_km": 5.0}


def main():
    print("== 1. 参数表 读写 / 模板生成 ==")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sensor_params.toml"
        sl.save_sensor_params([
            {"norad": 1, "fov_half_angle_deg": 20.0, "min_elevation_deg": 8.0,
             "swath_km": None},
            {"norad": 2, "fov_half_angle_deg": None, "min_elevation_deg": None,
             "swath_km": 185.0},
            {"norad": None, "fov_half_angle_deg": 5.0, "min_elevation_deg": 5.0,
             "swath_km": None},   # 无 norad：应被忽略
        ], str(p))
        rows = sl.load_sensor_params(str(p))
        check("保存->读取往返一致", len(rows) == 2 and rows[0]["norad"] == 1
              and rows[0]["fov_half_angle_deg"] == 20.0
              and rows[1]["swath_km"] == 185.0,
              str(rows))
        check("无 norad 空行忽略", all(r["norad"] is not None for r in rows))

        p2 = Path(td) / "auto.toml"
        made = sl.ensure_sensor_params(str(p2))
        check("ensure 自动生成模板", made.exists()
              and sl.load_sensor_params(str(p2)) == [])

    print("== 2. NORAD 匹配 / 优先级 / 重复编号 ==")
    ov = [
        {"norad": 12345, "fov_half_angle_deg": 15.0, "min_elevation_deg": None,
         "swath_km": None},
        {"norad": 12345, "fov_half_angle_deg": 40.0, "min_elevation_deg": 5.0,
         "swath_km": None},          # 重复编号：后写覆盖
        {"norad": 67890, "fov_half_angle_deg": None, "min_elevation_deg": None,
         "swath_km": 30.0},
    ]
    cfg = make_cfg()
    sat_cfg, info = cov.resolve_sat_cfg(99999, ov, cfg)
    check("未设置用全局默认", sat_cfg["fov_half_angle_deg"] == 30.0
          and info["source"] == "default" and info["swath_km"] == 0.0)
    sat_cfg, info = cov.resolve_sat_cfg(12345, ov, cfg)
    check("重复编号后写覆盖（40° 生效）",
          sat_cfg["fov_half_angle_deg"] == 40.0 and info["source"] == "override"
          and info["min_elevation_deg"] == 5.0)
    sat_cfg, info = cov.resolve_sat_cfg(67890, ov, cfg)
    check("幅宽覆盖：swath_km 进入 sat_cfg",
          sat_cfg.get("swath_km") == 30.0 and info["swath_km"] == 30.0)
    ov_sw = [{"norad": 777, "fov_half_angle_deg": 45.0, "min_elevation_deg": None,
              "swath_km": 100.0}]
    sat_cfg, info = cov.resolve_sat_cfg(777, ov_sw, cfg)
    check("同一行幅宽优先于视场半角",
          sat_cfg.get("swath_km") == 100.0 and info["fov_half_angle_deg"] is None)

    print("== 3. 幅宽换算数学 ==")
    eta = float(cov.swath_to_half_angle_deg(700.0, 185.0))
    check("185km/700km 等效半角约 7.5°", 7.4 <= eta <= 7.6, f"{eta:.2f}°")
    eta_small = float(cov.swath_to_half_angle_deg(700.0, 20.0))
    check("20km/700km 等效半角约 0.8°", 0.7 <= eta_small <= 0.9, f"{eta_small:.2f}°")

    print("== 4. 端到端：空表=现状 / 覆盖生效 / 幅宽生效 ==")
    recs = jilin_recs()
    check("吉林一号全量 58 颗（测试基准）", len(recs) == 58, f"{len(recs)} 颗")
    res0 = cov.analyze_collection_pipeline(
        recs, region(), make_cfg(), "2026-08-03 00:00:00",
        "2026-08-03 08:00:00", sensor_params=[])
    res_none = cov.analyze_collection_pipeline(
        recs, region(), make_cfg(), "2026-08-03 00:00:00",
        "2026-08-03 08:00:00", sensor_params=None)
    check("空表与不传参数结果一致", res0 is not None and res_none is not None
          and res0["n_win"] == res_none["n_win"],
          f"{res0['n_win'] if res0 else 0} vs {res_none['n_win'] if res_none else 0}")
    if res0:
        check("默认结果含 sat_params 且全部 source=default",
              len(res0["sat_params"]) == len(recs)
              and all(p["source"] == "default" for p in res0["sat_params"]))
        df_summary = res0["dfs"][1]
        check("汇总表补实际所用参数列",
              {"fov_used_deg", "min_elev_used_deg", "swath_used_km",
               "sensor_source"}.issubset(df_summary.columns))
        import json
        _meta0 = json.loads(Path("output/analysis_meta.json").read_text(encoding="utf-8"))
        check("默认结果元信息 sensor_override_count=0",
              _meta0.get("sensor_override_count", 0) == 0)

    first_norad = recs[0]["norad_id"]
    ov_tiny = [{"norad": first_norad, "fov_half_angle_deg": 1.0,
                "min_elevation_deg": 10.0, "swath_km": None}]
    res_small = cov.analyze_collection_pipeline(
        recs, region(), make_cfg(), "2026-08-03 00:00:00",
        "2026-08-03 08:00:00", sensor_params=ov_tiny)
    check("单星 fov=1° 覆盖窗口数不增（远小于 30°）",
          res_small is not None and res_small["n_win"] <= res0["n_win"],
          f"{res_small['n_win'] if res_small else 0} vs {res0['n_win']}")
    if res_small:
        p_first = next(p for p in res_small["sat_params"] if p["norad"] == first_norad)
        check("单星覆盖 source=override 且 fov=1°",
              p_first["source"] == "override" and p_first["fov_half_angle_deg"] == 1.0)
        import json
        _meta1 = json.loads(Path("output/analysis_meta.json").read_text(encoding="utf-8"))
        check("覆盖结果元信息 sensor_override_count=1",
              _meta1.get("sensor_override_count", 0) == 1)

    ov_sw = [{"norad": first_norad, "fov_half_angle_deg": None,
              "min_elevation_deg": 10.0, "swath_km": 20.0}]
    res_sw = cov.analyze_collection_pipeline(
        recs, region(), make_cfg(), "2026-08-03 00:00:00",
        "2026-08-03 08:00:00", sensor_params=ov_sw)
    check("单星幅宽 20km 覆盖窗口数不增（远小于 30° 口径）",
          res_sw is not None and res_sw["n_win"] <= res0["n_win"],
          f"{res_sw['n_win'] if res_sw else 0} vs {res0['n_win']}")
    if res_sw:
        p_sw = next(p for p in res_sw["sat_params"] if p["norad"] == first_norad)
        check("幅宽覆盖 source=override 且 swath=20km",
              p_sw["source"] == "override" and p_sw["swath_km"] == 20.0
              and p_sw["fov_half_angle_deg"] is None)

    print("== 5. 收藏夹批量添加 NORAD 提取 ==")
    cols = sl.load_builtin_collections()
    jl_col = next(c for c in cols if "吉林一号全量" in c["name"])
    ids = sl.collection_norad_ids(jl_col)
    check("吉林一号全量批量提取 58 个 NORAD", len(ids) == 58
          and all(isinstance(i, int) for i in ids), f"{len(ids)} 个")

    print("== 6. AppTest 首页 0 异常 ==")
    try:
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(Path(__file__).resolve().parent / "loader_app.py"),
                               default_timeout=60)
        at.run()
        check("AppTest 无异常", not at.exception)
    except Exception as e:
        check("AppTest 无异常", False, str(e))

    print("========================================")
    print(f"结果：{PASS} 项通过，{FAIL} 项失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
