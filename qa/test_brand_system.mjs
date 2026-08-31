import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const webRoot = new URL('../frontend/web/', import.meta.url);

function contrastRatio(foreground, background) {
  const luminance = (hex) => {
    const channels = hex.slice(1).match(/../g).map((channel) => parseInt(channel, 16) / 255);
    const [red, green, blue] = channels.map((channel) => channel <= 0.04045
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4);
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
  };
  const light = Math.max(luminance(foreground), luminance(background));
  const dark = Math.min(luminance(foreground), luminance(background));
  return (light + 0.05) / (dark + 0.05);
}

test('Archbro loads the approved modular A logo and favicon', async () => {
  const [page, logo] = await Promise.all([
    readFile(new URL('index.html', webRoot), 'utf8'),
    readFile(new URL('archbro-logo.svg', webRoot), 'utf8'),
  ]);

  assert.match(page, /<link rel="icon" href="\/static\/archbro-logo\.svg\?v=20260829-3" \/>/);
  assert.match(page, /<img class="brand-symbol" src="\/static\/archbro-logo\.svg\?v=20260829-3" alt="" \/>/);
  assert.match(logo, /<title>Archbro modular A logo<\/title>/);
  assert.match(logo, /<desc>A modular capital A with a four-point AI spark at the upper right\.<\/desc>/);

  for (const color of ['#8B5CF6', '#FF7A66', '#3B82F6', '#22B96B', '#5A49B8', '#FF593D']) {
    assert.ok(logo.includes(color), `logo is missing ${color}`);
  }

  assert.doesNotMatch(page, /brand-(?:frame|human|node|spark|spark-dot)/);
});

test('Archbro keeps its brand mark visible at narrow preview widths', async () => {
  const css = await readFile(new URL('styles.css', webRoot), 'utf8');

  assert.doesNotMatch(css, /\.brand(?:,[^{]+)?\{display:none(?:!important)?\}/);
});

test('Archbro renders the AI spark at 150 percent around its original center', async () => {
  const logo = await readFile(new URL('archbro-logo.svg', webRoot), 'utf8');
  const spark = logo.match(/<path id="ai-spark"[^>]+transform="translate\(470 145\) scale\(([\d.]+)\) translate\(-470 -145\)"/);

  assert.ok(spark, 'AI spark is missing its center-scaled transform');
  assert.equal(Number(spark[1]), 1.5);
});

test('Archbro exposes the approved palette with unambiguous status colors', async () => {
  const [css, progressCss, js] = await Promise.all([
    readFile(new URL('styles.css', webRoot), 'utf8'),
    readFile(new URL('progress.css', webRoot), 'utf8'),
    readFile(new URL('app.js', webRoot), 'utf8'),
  ]);

  for (const token of [
    '--brand-primary:#CBC3E3',
    '--brand-hover:#B8AADD',
    '--brand-soft:#F4F1FB',
    '--brand-strong:#7567A8',
    '--brand-deep:#4D416F',
    '--module-deep:#5A49B8',
    '--module-violet:#8B5CF6',
    '--module-coral:#FF7A66',
    '--module-blue:#3B82F6',
    '--module-green:#22B96B',
    '--module-orange:#FF593D',
    '--canvas:#F6F5F2',
    '--surface:#FFFFFF',
    '--ink:#1D1D1F',
    '--success:#237A57',
    '--warning:#A15C12',
    '--danger:#B42318',
  ]) {
    assert.ok(css.includes(token), `missing ${token}`);
  }

  assert.ok(css.includes('.btn.primary{background:var(--brand-primary);color:var(--ink)'));
  assert.ok(css.includes('.metric.blue{--metric-accent:var(--module-blue)'));
  assert.ok(css.includes('.metric.purple{--metric-accent:var(--module-violet)'));
  assert.ok(css.includes('.metric.green{--metric-accent:var(--success)'));
  assert.ok(css.includes('.metric.amber{--metric-accent:var(--warning)'));
  assert.ok(css.includes('.account-button{') && css.includes('background:#FFF0ED'));
  assert.match(css, /#graphCanvas \.node-card \.node-surface\{[^}]*fill:var\(--surface\)/);
  assert.match(css, /#graphCanvas \.node-name\{[^}]*fill:var\(--ink\)/);
  assert.doesNotMatch(js, /const palette =|const layerTitle =|const indegree =/);
  assert.doesNotMatch(css, /linear-gradient|radial-gradient|backdrop-filter/i);
  assert.doesNotMatch(css, /#7c3aed/i);
  assert.doesNotMatch(progressCss, /#7c3aed/i);
  assert.doesNotMatch(js, /#7c3aed/i);
});

test('product surfaces use the shared canvas tokens and semantic status colors', async () => {
  const css = await readFile(new URL('styles.css', webRoot), 'utf8');

  for (const selector of ['.entry-experience', '.entry-topbar', '.landing-composer', '.auth-card', '.preference-card', '.top-menu', '.account-settings-dialog']) {
    assert.match(css, new RegExp(`${selector.replace('.', '\\.')}`));
  }
  for (const token of ['var(--canvas)', 'var(--surface)', 'var(--brand-soft)', 'var(--brand-deep)', 'var(--success)', 'var(--warning)', 'var(--danger)']) {
    assert.ok(css.includes(token), `missing shared surface token ${token}`);
  }
  assert.doesNotMatch(css, /\.nav-item(?:[\s.{:#])/);
});

test('small brand and architecture text meets WCAG AA contrast', async () => {
  const [css, js] = await Promise.all([
    readFile(new URL('styles.css', webRoot), 'utf8'),
    readFile(new URL('app.js', webRoot), 'utf8'),
  ]);
  const token = (name) => css.match(new RegExp(`${name}:(#[0-9A-F]{6})`, 'i'))?.[1];

  assert.ok(contrastRatio(token('--ink'), token('--brand-primary')) >= 4.5);
  assert.ok(contrastRatio(token('--brand-deep'), token('--brand-primary')) >= 4.5);
  assert.ok(contrastRatio(token('--brand-deep'), token('--brand-soft')) >= 4.5);

  assert.ok(contrastRatio(token('--ink'), token('--surface')) >= 4.5);
  assert.match(css, /#graphCanvas \.node-card \.node-surface\{[^}]*fill:var\(--surface\)/);
  assert.match(css, /#graphCanvas \.node-name\{[^}]*fill:var\(--ink\)/);
  assert.doesNotMatch(js, /front\|web\|ui\|client[\s\S]+?return \{fill:/i);
});
