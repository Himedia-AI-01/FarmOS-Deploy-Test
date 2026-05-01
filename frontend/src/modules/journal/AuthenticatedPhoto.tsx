import { useEffect, useState } from "react";
import { API_BASE } from "@/utils/api";

interface Props {
  photoId: number;
  /** 썸네일 (?thumb=1) 여부. 미지정 시 원본. */
  thumb?: boolean;
  alt?: string;
  className?: string;
  onClick?: (e: React.MouseEvent<HTMLImageElement>) => void;
}

/**
 * 영농일지 첨부 사진을 표시하는 인증 image 컴포넌트.
 *
 * `<img src="...">` 직접 호출은 cross-origin 환경에서 cookie/credentials 가 따라가지
 * 못해 운영에서 401 로 깨질 수 있다. 본 컴포넌트는 fetch(credentials:"include") 로
 * blob 을 가져와 ObjectURL 로 렌더링하고 unmount 시 URL.revokeObjectURL 로 정리한다.
 *
 * 다운로드 중에는 회색 placeholder 가 보이고, 실패 시도 동일 placeholder 로 fallback.
 */
export default function AuthenticatedPhoto({
  photoId,
  thumb = false,
  alt = "",
  className,
  onClick,
}: Props) {
  const [src, setSrc] = useState<string>("");

  useEffect(() => {
    let active = true;
    let createdUrl: string | null = null;

    const url = thumb
      ? `${API_BASE}/journal/photos/${photoId}?thumb=1`
      : `${API_BASE}/journal/photos/${photoId}`;

    fetch(url, { credentials: "include" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.blob();
      })
      .then((blob) => {
        if (!active) {
          // 컴포넌트가 이미 unmount 된 케이스 — 만들지 않음
          return;
        }
        createdUrl = URL.createObjectURL(blob);
        setSrc(createdUrl);
      })
      .catch(() => {
        // 실패는 placeholder 유지 (silent — alt 가 SR 에 노출됨)
      });

    return () => {
      active = false;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [photoId, thumb]);

  if (!src) {
    return (
      <div
        className={`${className ?? ""} bg-gray-100`}
        role="img"
        aria-label={alt}
      />
    );
  }

  return (
    <img src={src} alt={alt} className={className} onClick={onClick} />
  );
}
