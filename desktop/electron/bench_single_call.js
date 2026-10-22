'use strict';
/**
 * 探测：一次 getSources 能不能同时拿到两块屏的「原生物理分辨率」缩略图。
 * 如果可以，串行两次调用就能省成一次。
 * 不创建任何窗口。
 */
const { app, screen, desktopCapturer } = require('electron');

app.whenReady().then(async () => {
  const displays = screen.getAllDisplays();
  const native = displays.map((d) => ({
    id: d.id,
    w: Math.round(d.bounds.width * d.scaleFactor),
    h: Math.round(d.bounds.height * d.scaleFactor),
  }));

  // 取所有屏里最大的物理尺寸
  const maxW = Math.max(...native.map((n) => n.w));
  const maxH = Math.max(...native.map((n) => n.h));
  console.log('原生物理尺寸:', native.map((n) => n.w + 'x' + n.h).join(', '));
  console.log('一次调用用的 thumbnailSize:', maxW + 'x' + maxH);

  const t = Date.now();
  const sources = await desktopCapturer.getSources({
    types: ['screen'],
    thumbnailSize: { width: maxW, height: maxH },
    fetchWindowIcons: false,
  });
  console.log('一次调用耗时: ' + (Date.now() - t) + ' ms');

  console.log('\n拿到的每张缩略图实际尺寸:');
  for (const n of native) {
    const s = sources.find((x) => String(x.display_id) === String(n.id));
    if (!s) { console.log('  display ' + n.id + ': 没找到'); continue; }
    const sz = s.thumbnail.getSize();
    const exact = (sz.width === n.w && sz.height === n.h);
    console.log('  display ' + n.id + ': 期望 ' + n.w + 'x' + n.h
      + '  实际 ' + sz.width + 'x' + sz.height + (exact ? '  ✓ 原生' : '  ✗ 不是原生'));
  }

  app.quit();
}).catch((e) => { console.error(e); app.quit(); });
