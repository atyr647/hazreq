// iOS-aware download. Standard <a download> works on desktop browsers
// but iOS Safari has historically ignored the download attribute on
// blob: URLs and dropped user-gesture context across awaits.
//
// Caller MUST invoke this from a real user-gesture handler (click,
// submit, etc.) — async work consumes the gesture token on iOS.

const isIos = () => /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;

export async function safeSave(blob, filename) {
  if (isIos() && navigator.canShare && navigator.canShare({ files: [new File([blob], filename, { type: blob.type })] })) {
    try {
      await navigator.share({
        files: [new File([blob], filename, { type: blob.type })],
        title: filename,
      });
      return 'share';
    } catch (e) {
      // user cancelled or share unavailable — fall through to download
    }
  }
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, 0);
  return 'download';
}
