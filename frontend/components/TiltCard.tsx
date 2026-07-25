"use client";

import { motion, useMotionTemplate, useMotionValue, useSpring } from "framer-motion";
import { type ReactNode, useRef } from "react";

/**
 * A card that tilts towards the cursor.
 *
 * Real 3D via CSS `perspective` and `rotateX/rotateY` — the browser composites it
 * on the GPU, so it costs a transform per frame and no library. The rotation is
 * spring-damped and capped at a few degrees; anything more looks like a novelty
 * and makes text hard to read.
 *
 * Disabled for pointer devices that cannot hover (touch), where there is no
 * cursor to track and the effect would only fire on tap.
 */

const MAX_TILT_DEGREES = 5;

export function TiltCard({
  children,
  delay = 0,
  className = "",
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const rotateX = useSpring(useMotionValue(0), { stiffness: 200, damping: 22 });
  const rotateY = useSpring(useMotionValue(0), { stiffness: 200, damping: 22 });
  const transform = useMotionTemplate`perspective(900px) rotateX(${rotateX}deg) rotateY(${rotateY}deg)`;

  const handleMove = (event: React.MouseEvent<HTMLDivElement>) => {
    const element = ref.current;
    if (!element || !window.matchMedia("(hover: hover)").matches) return;

    const bounds = element.getBoundingClientRect();
    // Cursor position as -0.5..0.5 from the card's centre.
    const offsetX = (event.clientX - bounds.left) / bounds.width - 0.5;
    const offsetY = (event.clientY - bounds.top) / bounds.height - 0.5;

    // Inverted on X so the card leans towards the cursor, not away from it.
    rotateX.set(-offsetY * MAX_TILT_DEGREES * 2);
    rotateY.set(offsetX * MAX_TILT_DEGREES * 2);
  };

  const handleLeave = () => {
    rotateX.set(0);
    rotateY.set(0);
  };

  return (
    <motion.div
      ref={ref}
      initial={{ opacity: 0, y: 14 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.45, delay }}
      onMouseMove={handleMove}
      onMouseLeave={handleLeave}
      style={{ transform }}
      className={`rounded-2xl border border-brand-100 bg-white/70 p-6 shadow-depth backdrop-blur transition-shadow hover:shadow-depth-lg ${className}`}
    >
      {children}
    </motion.div>
  );
}
