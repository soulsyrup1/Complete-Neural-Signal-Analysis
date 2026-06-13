import { getChannelIndex, getFrame, onChannelChange, onFrameChange } from "../state.js";
import { createDisposables } from "../disposables.js";
import {
  ACTIVE_COLOR,
  CHART_BACKGROUND,
  GRID_COLOR,
  MONO_FONT,
  MUTED_COLOR,
  clear,
  formatNumber,
  observeCanvas,
  resizeCanvas,
} from "./chart-utils.js";

export function initKuramotoView(root, data) {
  const section = root?.closest("section");
  if (!root) return () => {};
  if (section) section.hidden = false;
  const kuramoto0 = data?.kuramoto ?? null;
  const validKuramoto = kuramoto0
    && Array.isArray(kuramoto0.time)
    && Array.isArray(kuramoto0.channel_phases)
    && Array.isArray(kuramoto0.order_parameter_r)
    && Array.isArray(kuramoto0.mean_phase_psi)
    && kuramoto0.time.length > 1
    && kuramoto0.channel_phases.length > 0;
  if (!validKuramoto) {
    renderUnavailable(root, "Kuramoto Animation", "This dataset did not include Kuramoto phase arrays. Re-run Convert or Analyze in NeuroMouse with v0.11.0 so the backend generates alpha-band phase synchrony from the uploaded continuous signal.");
    return () => {};
  }

  const disposables = createDisposables();
  const canvas = document.createElement("canvas");
  canvas.className = "chart chart-kuramoto";
  canvas.width = 460;
  canvas.height = 420;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", "Kuramoto phase oscillator animation");
  root.innerHTML = "";
  root.append(canvas);

  function draw() {
    const { ctx, width, height } = resizeCanvas(canvas);
    clear(ctx, width, height);
    drawDotGrid(ctx, width, height);

    const frame = Math.min(data.kuramoto.time.length - 1, getFrame());
    const selectedIndex = getChannelIndex();
    const cx = width / 2;
    const cy = height / 2;
    const radius = Math.min(width, height) * 0.34;
    const rValue = data.kuramoto.order_parameter_r[frame] ?? 0;
    const psi = data.kuramoto.mean_phase_psi[frame] ?? 0;

    ctx.save();
    ctx.strokeStyle = GRID_COLOR;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(cx - radius, cy);
    ctx.lineTo(cx + radius, cy);
    ctx.moveTo(cx, cy - radius);
    ctx.lineTo(cx, cy + radius);
    ctx.stroke();

    ctx.strokeStyle = `rgba(0,212,160,${0.32 + rValue * 0.68})`;
    ctx.lineWidth = 2.2;
    ctx.shadowColor = "rgba(0,212,160,0.5)";
    ctx.shadowBlur = 9;
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.lineTo(cx + radius * rValue * Math.cos(psi), cy + radius * rValue * Math.sin(psi));
    ctx.stroke();
    ctx.shadowBlur = 0;

    for (let channel = 0; channel < data.kuramoto.channel_phases.length; channel += 1) {
      const phase = data.kuramoto.channel_phases[channel][frame] ?? 0;
      const x = cx + radius * Math.cos(phase);
      const y = cy + radius * Math.sin(phase);
      const selected = channel === selectedIndex;
      ctx.fillStyle = selected ? ACTIVE_COLOR : "rgba(0,180,130,0.58)";
      ctx.strokeStyle = selected ? CHART_BACKGROUND : "rgba(255,255,255,0.12)";
      ctx.lineWidth = selected ? 2 : 1;
      ctx.beginPath();
      ctx.arc(x, y, selected ? 6 : 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }

    ctx.fillStyle = MUTED_COLOR;
    ctx.font = `11px ${MONO_FONT}`;
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    ctx.fillText(`r = ${formatNumber(rValue, 3)}`, 14, 14);
    ctx.fillText(`psi = ${formatNumber(psi, 3)}`, 14, 30);
    ctx.fillText(`t = ${formatNumber(data.kuramoto.time[frame], 2)}s`, 14, 46);
    ctx.restore();
  }

  disposables.add(onFrameChange(draw));
  disposables.add(onChannelChange(draw));
  disposables.add(observeCanvas(canvas, draw));
  return disposables.dispose;
}

function drawDotGrid(ctx, width, height) {
  ctx.save();
  ctx.fillStyle = "rgba(255,255,255,0.025)";
  for (let x = 0; x < width; x += 20) {
    for (let y = 0; y < height; y += 20) {
      ctx.fillRect(x, y, 1, 1);
    }
  }
  ctx.restore();
}

function renderUnavailable(root, title, message) {
  root.innerHTML = `<div class="view-error" role="status"><strong>${title}</strong><span>${message}</span></div>`;
}
