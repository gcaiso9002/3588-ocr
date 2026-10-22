'use strict';
/**
 * 抓屏延迟基准测试（不开任何窗口，不会遮挡桌面）。
 *
 * 量两件事：
 *   1. 串行抓两块屏要多久（v1.2.0 的写法）
 *   2. 并行抓两块屏要多久（v1.2.1 的写法）
 *
 * 运行：npx electron bench_capture.js
 */
const { app, screen, desktopCapturer, nativeImage } = require('electron');

function ms(t) { return Date.now() - t; }

function sizeOf(pngBuffer) {
  const img = nativeImage.createFromBuffer(pngBuffer);
  const s = img.getSize();
  return { w: s.width, h: s.height };
}

async function grab(display, size) {
  const sources = await desktopCapturer.getSources({
    types: ['screen'],
    thumbnailSize: size,
    fetchWindowIcons: false,
  });
  let src = sources.find((s) => String(s.display_id) === String(display.id));
  if (!src) src = sources.find((s) => s.thumbnail && !s.thumbnail.isEmpty());
  if (!src) return null;
  return src.thumbnail.toPNG();
}

app.whenReady().then(async () => {
  const displays = screen.getAllDisplays();
  const sizes = displays.map((d) => ({
    width: Math.max(1, Math.round(d.bounds.width * d.scaleFactor)),
    height: Math.max(1, Math.round(d.bounds.height * d.scaleFactor)),
  }));

  console.log('显示器数量: ' + displays.length);
  displays.forEach((d, i) => {
    console.log('  display ' + d.id + '  ' + d.bounds.width + 'x' + d.bounds.height
      + ' @' + d.scaleFactor + '  ->  物理 ' + sizes[i].width + 'x' + sizes[i].height);
  });

  // 串行
  let t = Date.now();
  for (let i = 0; i < displays.length; i++) {
    await grab(displays[i], sizes[i]);
  }
  const serial = ms(t);
  console.log('\n串行抓屏（v1.2.0 写法）: ' + serial + ' ms');

  // 并行
  t = Date.now();
  const par = await Promise.all(displays.map((d, i) => grab(d, sizes[i])));
  const parallel = ms(t);
  console.log('并行抓屏（v1.2.1 写法）: ' + parallel + ' ms');

  // 关键校验：并行拿到的每块屏必须仍是「原生物理分辨率」，
  // 否则裁剪坐标会偏、OCR 精度会掉
  console.log('\n并行路径下每块屏的实际分辨率:');
  let allNative = true;
  displays.forEach((d, i) => {
    const b = par[i];
    const size = b ? sizeOf(b) : null;
    const ok = size && size.w === sizes[i].width && size.h === sizes[i].height;
    if (!ok) allNative = false;
    console.log('  display ' + d.id + ': 期望 ' + sizes[i].width + 'x' + sizes[i].height
      + '  实际 ' + (size ? size.w + 'x' + size.h : '?') + (ok ? '  ✓ 原生' : '  ✗'));
  });
  console.log('结论: ' + (allNative ? '两块屏都是原生分辨率，裁剪坐标不受影响'
    : '有屏幕不是原生分辨率，必须改回逐屏单独抓'));

  console.log('提速: ' + serial + ' -> ' + parallel + ' ms  (省了 '
    + (serial - parallel) + ' ms, ' + (parallel > 0 ? Math.round(serial / parallel * 10) / 10 : '-') + 'x)');

  // 再跑两轮取稳定值
  for (let k = 0; k < 2; k++) {
    t = Date.now();
    await Promise.all(displays.map((d, i) => grab(d, sizes[i])));
    console.log('  并行第 ' + (k + 2) + ' 轮: ' + ms(t) + ' ms');
  }

  app.quit();
}).catch((e) => {
  console.error(e);
  app.quit();
});
