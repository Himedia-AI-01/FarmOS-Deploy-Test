"""영농일지 Vision 입력용 EXIF 추출 유틸.

사진 bytes에서 촬영시각/GPS를 hint로 뽑아 LLM prompt에 주입한다.
EXIF 누락(스크린샷·PNG·소셜 업로드)이나 파싱 실패는 흐름을 깨지 않도록
모두 빈 ExifHint로 fallback 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any

from PIL import ExifTags, Image


@dataclass
class ExifHint:
    """LLM에 hint로 전달할 EXIF 메타.

    GPS 좌표가 있어도 필지 매핑은 V2에서 처리한다 — 본 단계에선 prompt에
    "GPS=37.51,127.05" 같은 텍스트로만 넘긴다.
    """

    taken_at: datetime | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    has_exif: bool = False


def extract_exif(image_bytes: bytes) -> ExifHint:
    """이미지 bytes에서 EXIF를 안전하게 추출."""
    try:
        img = Image.open(BytesIO(image_bytes))
    except Exception:
        return ExifHint()

    try:
        exif = img.getexif()
    except Exception:
        return ExifHint()

    if not exif:
        return ExifHint()

    tag_map: dict[str, Any] = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}

    taken_at = _parse_datetime(
        tag_map.get("DateTimeOriginal") or tag_map.get("DateTime")
    )

    gps_lat: float | None = None
    gps_lon: float | None = None
    try:
        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo) if hasattr(ExifTags, "IFD") else {}
    except Exception:
        gps_ifd = {}

    if gps_ifd:
        gps_lat, gps_lon = _parse_gps(gps_ifd)

    return ExifHint(
        taken_at=taken_at,
        gps_lat=gps_lat,
        gps_lon=gps_lon,
        has_exif=True,
    )


def _parse_datetime(raw: Any) -> datetime | None:
    """EXIF 'YYYY:MM:DD HH:MM:SS' 포맷 파싱."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_gps(gps_ifd: dict) -> tuple[float | None, float | None]:
    """GPS IFD에서 (lat, lon) decimal 변환.

    EXIF는 (degrees, minutes, seconds) 분수 + 'N'/'S'/'E'/'W' ref 형태.
    """
    try:
        lat_ref = gps_ifd.get(1)  # GPSLatitudeRef
        lat_val = gps_ifd.get(2)  # GPSLatitude
        lon_ref = gps_ifd.get(3)  # GPSLongitudeRef
        lon_val = gps_ifd.get(4)  # GPSLongitude

        lat = _dms_to_decimal(lat_val, lat_ref) if lat_val and lat_ref else None
        lon = _dms_to_decimal(lon_val, lon_ref) if lon_val and lon_ref else None
        return lat, lon
    except Exception:
        return None, None


def _coord_component_to_float(v: Any) -> float:
    """EXIF GPS 좌표 요소를 float 로 변환.

    Pillow 가 GPS IFD 의 RATIONAL 값을 반환하는 형태가 다양:
    - IFDRational 객체 (numerator/denominator 속성)
    - (num, den) 분수 튜플 (Pillow 8 이전)
    - 단순 float/int
    """
    if hasattr(v, "numerator") and hasattr(v, "denominator"):
        denom = v.denominator
        return v.numerator / denom if denom else 0.0
    if isinstance(v, tuple) and len(v) == 2:
        num, denom = v
        return num / denom if denom else 0.0
    return float(v)


def _dms_to_decimal(dms: Any, ref: Any) -> float | None:
    """((deg, min, sec), 'N'|'S'|'E'|'W') → decimal degrees.

    각 deg/min/sec 는 IFDRational, (num, den) 분수, 또는 단순 숫자 모두 허용.
    """
    try:
        d, m, s = (_coord_component_to_float(x) for x in dms)
        decimal = d + m / 60.0 + s / 3600.0
        if isinstance(ref, bytes):
            ref = ref.decode("ascii", errors="ignore")
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 6)
    except Exception:
        return None


def build_exif_summary(hints: list[ExifHint]) -> str:
    """LLM prompt에 주입할 사진별 EXIF 요약 텍스트."""
    if not hints:
        return "(EXIF 메타 없음)"

    lines: list[str] = []
    for idx, h in enumerate(hints, start=1):
        if not h.has_exif:
            lines.append(f"사진{idx}: EXIF 없음")
            continue
        parts: list[str] = []
        if h.taken_at:
            parts.append(f"촬영시각={h.taken_at.isoformat()}")
        if h.gps_lat is not None and h.gps_lon is not None:
            parts.append(f"GPS={h.gps_lat:.4f},{h.gps_lon:.4f}")
        lines.append(f"사진{idx}: " + (", ".join(parts) if parts else "EXIF 비어있음"))
    return "\n".join(lines)
