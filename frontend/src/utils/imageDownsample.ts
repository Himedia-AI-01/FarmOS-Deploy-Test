/**
 * 이미지 다운샘플 — 사용자가 올리는 큰 사진(스마트폰 12MP+)을
 * 업로드 전에 긴 변 maxSide 픽셀, JPEG quality 로 줄여서 전송한다.
 *
 * - 영농일지 사진 분석/첨부 두 경로(parse-photos, /journal/photos) 가 동일 함수 사용.
 * - 이미 충분히 작거나 maxSide 이내면 원본 그대로 반환 (네트워크 비용 회피).
 * - OffscreenCanvas 미지원 환경(구형 사파리 등)은 일반 Canvas 로 fallback.
 * - 어떤 단계에서든 실패하면 원본 File 을 그대로 반환 — BE 단의 max bytes 검증이
 *   2차 안전망 역할.
 */
export async function downsampleImage(
  file: File,
  maxSide = 1280,
  quality = 0.85,
): Promise<File> {
  // 너무 작은 사진은 다운샘플 비용이 더 큼
  if (file.size < 200_000) return file;

  // 조기 return / 예외 경로에서도 ImageBitmap 자원이 반드시 해제되도록 try/finally 로
  // close() 보장 (이미지 1장당 수~수십 MB 점유 가능).
  let bitmap: ImageBitmap | null = null;
  try {
    bitmap = await createImageBitmap(file);
    const longest = Math.max(bitmap.width, bitmap.height);
    const ratio = Math.min(1, maxSide / longest);
    if (ratio === 1 && file.size < 1_000_000) return file;

    const w = Math.round(bitmap.width * ratio);
    const h = Math.round(bitmap.height * ratio);

    let blob: Blob | null = null;
    if (typeof OffscreenCanvas !== "undefined") {
      const canvas = new OffscreenCanvas(w, h);
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("canvas 2d context 실패");
      ctx.drawImage(bitmap, 0, 0, w, h);
      blob = await canvas.convertToBlob({ type: "image/jpeg", quality });
    } else {
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("canvas 2d context 실패");
      ctx.drawImage(bitmap, 0, 0, w, h);
      blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", quality),
      );
    }
    if (!blob) return file;

    const newName = file.name.replace(/\.[^.]+$/, ".jpg");
    return new File([blob], newName, { type: "image/jpeg" });
  } catch {
    return file;
  } finally {
    bitmap?.close?.();
  }
}
