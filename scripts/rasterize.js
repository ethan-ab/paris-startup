// Rasterise SVG logos to PNG with the Playwright Chromium, for scripts/logos.py.
// Reads lines "in.svg\tout.png" on stdin, renders each at 256 px on its longest side (transparent), prints "ok"/"err" per line.
const { chromium } = require('playwright');
const fs = require('fs');
const readline = require('readline');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 600, height: 600 } });
  const rl = readline.createInterface({ input: process.stdin });
  for await (const line of rl) {
    const [src, out] = line.split('\t');
    try {
      const b64 = fs.readFileSync(src).toString('base64');
      await page.setContent(`<html><body style="margin:0;background:transparent">
        <img id="i" src="data:image/svg+xml;base64,${b64}"></body></html>`);
      const box = await page.evaluate(async () => {
        const i = document.getElementById('i');
        await i.decode();
        let w = i.naturalWidth || 256, h = i.naturalHeight || 256;
        const s = 256 / Math.max(w, h); w = Math.round(w * s); h = Math.round(h * s);
        i.style.width = w + 'px'; i.style.height = h + 'px'; i.style.display = 'block';
        return { w, h };
      });
      if (!box.w || !box.h) throw new Error('empty');
      await page.screenshot({ path: out, omitBackground: true, clip: { x: 0, y: 0, width: box.w, height: box.h } });
      console.log('ok');
    } catch (e) {
      console.log('err');
    }
  }
  await browser.close();
})();
