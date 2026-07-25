"use client";

import { useEffect, useRef } from "react";

/**
 * The landing page's hero animation: money entering at the top and sorting
 * itself into labelled category streams.
 *
 * Hand-written on a 2D canvas rather than pulled from a 3D library. three.js is
 * roughly 600 KB gzipped, which is more than the rest of this app, and a
 * personal-finance tool that takes three seconds to paint on a phone reads as
 * unserious regardless of how good the shader is. The depth here comes from
 * perspective projection, size falloff and layered blur — cheap, and it holds up
 * on a mid-range Android.
 *
 * Honours `prefers-reduced-motion` by drawing one static frame.
 */

type Particle = {
  /** Position along its stream, 0 at the source, 1 at the basin. */
  progress: number;
  speed: number;
  /** Which stream this particle belongs to. */
  stream: number;
  /** Lateral jitter so streams look like flows, not laser beams. */
  wobble: number;
  wobbleSpeed: number;
  size: number;
  depth: number;
};

const STREAMS = [
  { label: "food", color: "#EA580C", share: 0.24 },
  { label: "rent", color: "#9333EA", share: 0.2 },
  { label: "transport", color: "#0EA5E9", share: 0.15 },
  { label: "shopping", color: "#F59E0B", share: 0.14 },
  { label: "bills", color: "#0D9488", share: 0.12 },
  { label: "everything else", color: "#64748B", share: 0.15 },
];

const PARTICLE_COUNT = 150;
const SOURCE_Y = 0.1;
const BASIN_Y = 0.82;

export function MoneyFlowCanvas({ className = "" }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Weighted pick so stream density reflects its share of spending — the
    // animation is a rough bar chart that happens to move.
    const cumulative: number[] = [];
    STREAMS.reduce((total, stream, index) => {
      cumulative[index] = total + stream.share;
      return cumulative[index];
    }, 0);
    const pickStream = () => {
      const roll = Math.random();
      return cumulative.findIndex((edge) => roll <= edge);
    };

    const spawn = (seeded: boolean): Particle => ({
      progress: seeded ? Math.random() : 0,
      speed: 0.0022 + Math.random() * 0.0034,
      stream: pickStream(),
      wobble: Math.random() * Math.PI * 2,
      wobbleSpeed: 0.01 + Math.random() * 0.02,
      size: 1.6 + Math.random() * 2.6,
      depth: 0.45 + Math.random() * 0.55,
    });

    let particles = Array.from({ length: PARTICLE_COUNT }, () => spawn(true));

    let width = 0;
    let height = 0;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      // Cap the pixel ratio: on a 3x phone display a full-bleed canvas at native
      // density costs more than the animation is worth.
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = rect.width;
      height = rect.height;
      canvas.width = Math.floor(width * ratio);
      canvas.height = Math.floor(height * ratio);
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    };

    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    /** Where a stream lands along the bottom, in 0..1. */
    const basinX = (stream: number) => (stream + 0.5) / STREAMS.length;

    const draw = () => {
      ctx.clearRect(0, 0, width, height);

      const sourceX = width / 2;
      const sourceY = height * SOURCE_Y;
      const basinY = height * BASIN_Y;

      // Streams, drawn as soft tapering ribbons behind the particles.
      STREAMS.forEach((stream, index) => {
        const endX = basinX(index) * width;
        const gradient = ctx.createLinearGradient(sourceX, sourceY, endX, basinY);
        gradient.addColorStop(0, `${stream.color}00`);
        gradient.addColorStop(0.45, `${stream.color}22`);
        gradient.addColorStop(1, `${stream.color}44`);

        ctx.beginPath();
        ctx.moveTo(sourceX, sourceY);
        ctx.bezierCurveTo(
          sourceX, sourceY + (basinY - sourceY) * 0.55,
          endX, sourceY + (basinY - sourceY) * 0.5,
          endX, basinY,
        );
        ctx.strokeStyle = gradient;
        ctx.lineWidth = 2 + stream.share * 26;
        ctx.lineCap = "round";
        ctx.stroke();
      });

      // Basin markers.
      STREAMS.forEach((stream, index) => {
        const x = basinX(index) * width;
        ctx.beginPath();
        ctx.arc(x, basinY, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = stream.color;
        ctx.globalAlpha = 0.85;
        ctx.fill();
        ctx.globalAlpha = 1;
      });

      // Particles.
      for (const particle of particles) {
        const stream = STREAMS[particle.stream];
        const endX = basinX(particle.stream) * width;
        const t = particle.progress;

        // Same cubic Bézier as the ribbon, so particles ride their own stream.
        const inv = 1 - t;
        const x =
          inv * inv * inv * sourceX +
          3 * inv * inv * t * sourceX +
          3 * inv * t * t * endX +
          t * t * t * endX;
        const y =
          inv * inv * inv * sourceY +
          3 * inv * inv * t * (sourceY + (basinY - sourceY) * 0.55) +
          3 * inv * t * t * (sourceY + (basinY - sourceY) * 0.5) +
          t * t * t * basinY;

        const wobbleX = Math.sin(particle.wobble) * 9 * (1 - t) * particle.depth;
        // Fade in at the source, out as it lands.
        const alpha = Math.min(1, t * 5) * (1 - t * 0.55) * particle.depth;

        ctx.beginPath();
        ctx.arc(x + wobbleX, y, particle.size * particle.depth, 0, Math.PI * 2);
        ctx.fillStyle = stream.color;
        ctx.globalAlpha = alpha;
        ctx.fill();
        ctx.globalAlpha = 1;
      }

      // The source, drawn last so it sits on top.
      const glow = ctx.createRadialGradient(sourceX, sourceY, 0, sourceX, sourceY, 34);
      glow.addColorStop(0, "#EA580Ccc");
      glow.addColorStop(1, "#EA580C00");
      ctx.beginPath();
      ctx.arc(sourceX, sourceY, 34, 0, Math.PI * 2);
      ctx.fillStyle = glow;
      ctx.fill();

      ctx.beginPath();
      ctx.arc(sourceX, sourceY, 7, 0, Math.PI * 2);
      ctx.fillStyle = "#0F172A";
      ctx.fill();
    };

    let frame = 0;
    const tick = () => {
      for (const particle of particles) {
        particle.progress += particle.speed;
        particle.wobble += particle.wobbleSpeed;
      }
      // Respawn finished particles rather than allocating new arrays each frame.
      particles = particles.map((particle) =>
        particle.progress >= 1 ? spawn(false) : particle,
      );
      draw();
      frame = requestAnimationFrame(tick);
    };

    if (reduceMotion) {
      draw();
    } else {
      frame = requestAnimationFrame(tick);
    }

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, []);

  return (
    <div className={`relative ${className}`}>
      <canvas
        ref={canvasRef}
        className="h-full w-full"
        // Decorative: the surrounding text carries the meaning.
        aria-hidden="true"
      />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-around px-2 pb-1">
        {STREAMS.map((stream) => (
          <span
            key={stream.label}
            className="truncate text-[10px] font-medium tracking-wide text-ink-muted sm:text-xs"
          >
            {stream.label}
          </span>
        ))}
      </div>
    </div>
  );
}
