"""服务配置。一个数据目录只允许一个 API 实例和一个计算 worker。"""
from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    project_root: Path = Path(__file__).resolve().parents[2]
    state_dir: Path = Path('output/api')
    max_pending: int = 8
    job_timeout_s: float = 900
    min_grid_km: float = 2.5
    max_horizon_h: float = 24
    min_detection_step_s: int = 5
    max_grid_cells: int = 5000
    max_grid_time_cells: int = 2_000_000
    api_token: str = ''
    cors_origins: tuple[str, ...] = ()

    def __post_init__(self):
        if self.max_pending < 1 or self.job_timeout_s <= 0:
            raise ValueError('队列容量和任务时限必须大于零')
        if self.min_grid_km <= 0 or self.max_horizon_h <= 0 or self.min_detection_step_s < 1 or min(self.max_grid_cells, self.max_grid_time_cells) < 1:
            raise ValueError('服务资源限制配置不合法')

    @classmethod
    def from_env(cls):
        root = Path(os.environ.get('SAT_PROJECT_ROOT', Path(__file__).resolve().parents[2])).resolve()
        return cls(
            project_root=root,
            state_dir=Path(os.environ.get('SAT_STATE_DIR', root / 'output/api')).resolve(),
            max_pending=int(os.environ.get('SAT_MAX_PENDING', '8')),
            job_timeout_s=float(os.environ.get('SAT_JOB_TIMEOUT_S', '900')),
            min_grid_km=float(os.environ.get('SAT_MIN_GRID_KM', '2.5')),
            max_horizon_h=float(os.environ.get('SAT_MAX_HORIZON_H', '24')),
            min_detection_step_s=int(os.environ.get('SAT_MIN_DETECTION_STEP_S', '5')),
            max_grid_cells=int(os.environ.get('SAT_MAX_GRID_CELLS', '5000')),
            max_grid_time_cells=int(os.environ.get('SAT_MAX_GRID_TIME_CELLS', '2000000')),
            api_token=os.environ.get('SAT_API_TOKEN', ''),
            cors_origins=tuple(x.strip() for x in os.environ.get('SAT_CORS_ORIGINS', '').split(',') if x.strip()),
        )
