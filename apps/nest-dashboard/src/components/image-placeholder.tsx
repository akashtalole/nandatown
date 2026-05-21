/* ------------------------------------------------------------------ *
 *  ImagePlaceholder
 *
 *  Visible placeholder for an image we plan to generate with an AI
 *  image model.  The full prompt is shown inline so it can be copied
 *  straight into Midjourney / Imagen / DALL·E.
 *
 *  Use:
 *    <ImagePlaceholder
 *      id="hero-mesh"
 *      ratio="16/9"
 *      prompt="abstract organic network of warm rust lines …"
 *    />
 * ------------------------------------------------------------------ */

interface ImagePlaceholderProps {
  /** Short reference id, shown in the corner. */
  id: string;
  /** Aspect ratio (CSS, e.g. "16/9", "4/3", "1/1"). Default "16/9". */
  ratio?: string;
  /** Full prompt — shown inside the placeholder. */
  prompt: string;
  /** Optional caption shown below the box. */
  caption?: string;
  /** Tone: "warm" (cream paper) or "ink" (dark surface). Default warm. */
  tone?: "warm" | "ink";
  /** Optional tailwind classes for the outer wrapper. */
  className?: string;
}

export function ImagePlaceholder({
  id,
  ratio = "16/9",
  prompt,
  caption,
  tone = "warm",
  className = "",
}: ImagePlaceholderProps) {
  const isInk = tone === "ink";

  return (
    <figure className={className}>
      <div
        className={`relative overflow-hidden rounded-2xl border ${
          isInk
            ? "border-ink-700 bg-ink-800 text-cream-100"
            : "border-cream-400 bg-cream-200 text-ink-600"
        }`}
        style={{ aspectRatio: ratio }}
      >
        {/* Subtle hatch pattern so the placeholder reads as "to-fill" */}
        <div
          aria-hidden
          className="absolute inset-0 opacity-[0.35]"
          style={{
            backgroundImage: isInk
              ? "repeating-linear-gradient(45deg, rgba(255,255,255,0.04) 0 1px, transparent 1px 12px)"
              : "repeating-linear-gradient(45deg, rgba(20,19,18,0.06) 0 1px, transparent 1px 12px)",
          }}
        />

        {/* Corner label */}
        <div className="absolute top-4 left-4 flex items-center gap-2">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              isInk ? "bg-rust-soft" : "bg-rust"
            }`}
          />
          <span
            className={`font-mono text-[10px] uppercase tracking-[0.18em] ${
              isInk ? "text-cream-200" : "text-ink-400"
            }`}
          >
            Image {id}
          </span>
        </div>

        {/* Aspect-ratio label, opposite corner */}
        <div
          className={`absolute top-4 right-4 font-mono text-[10px] uppercase tracking-[0.18em] ${
            isInk ? "text-cream-200/70" : "text-ink-300"
          }`}
        >
          {ratio}
        </div>

        {/* Prompt body */}
        <div className="absolute inset-0 flex flex-col items-start justify-end p-6 sm:p-8">
          <p
            className={`max-w-[42ch] font-display text-[clamp(0.95rem,1.3vw,1.2rem)] leading-snug italic ${
              isInk ? "text-cream-100" : "text-ink-700"
            }`}
          >
            &ldquo;{prompt}&rdquo;
          </p>
        </div>
      </div>

      {caption && (
        <figcaption
          className={`mt-3 font-mono text-[11px] uppercase tracking-[0.18em] ${
            isInk ? "text-ink-300" : "text-ink-300"
          }`}
        >
          {caption}
        </figcaption>
      )}
    </figure>
  );
}
