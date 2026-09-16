import type { ReactElement } from "react";

const PIECE_GLYPH: Record<string, string> = {
  K: "♔", Q: "♕", R: "♖", B: "♗", N: "♘", P: "♙",
  k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟",
};

export function Chessboard({ fen, size = 280, flipped = false }: { fen: string; size?: number; flipped?: boolean }) {
  const sq = size / 8;
  const ranks = (fen || "").split(" ")[0].split("/");
  const squares: ReactElement[] = [];
  const pieces: ReactElement[] = [];

  const displayRow = (r: number) => (flipped ? 7 - r : r);
  const displayCol = (f: number) => (flipped ? 7 - f : f);

  for (let r = 0; r < 8; r++) {
    for (let f = 0; f < 8; f++) {
      const light = (r + f) % 2 === 0;
      const dr = displayRow(r), df = displayCol(f);
      squares.push(
        <rect
          key={`sq-${r}-${f}`}
          x={df * sq} y={dr * sq} width={sq} height={sq}
          fill={light ? "var(--surface-2)" : "var(--bezel)"}
        />
      );
    }
  }

  for (let r = 0; r < Math.min(8, ranks.length); r++) {
    let file = 0;
    for (const ch of ranks[r]) {
      if (/[1-8]/.test(ch)) { file += parseInt(ch, 10); continue; }
      const glyph = PIECE_GLYPH[ch] || "";
      const isWhite = ch === ch.toUpperCase();
      const dr = displayRow(r), df = displayCol(file);
      const x = df * sq + sq / 2;
      const y = dr * sq + sq / 2;
      pieces.push(
        <text
          key={`p-${r}-${file}`}
          x={x} y={y}
          fontSize={sq * 0.74}
          textAnchor="middle"
          dominantBaseline="central"
          fill={isWhite ? "#f4f1ea" : "#18140f"}
          stroke={isWhite ? "#18140f" : "none"}
          strokeWidth={isWhite ? sq * 0.02 : 0}
        >
          {glyph}
        </text>
      );
      file++;
    }
  }

  return (
    <svg
      viewBox={`0 0 ${size} ${size}`}
      width="100%"
      style={{ maxWidth: size, aspectRatio: "1 / 1", display: "block" }}
      role="img"
      aria-label="Chess position"
    >
      {squares}
      {pieces}
      <rect x={1} y={1} width={size - 2} height={size - 2} fill="none" stroke="var(--bezel)" strokeWidth={2} />
    </svg>
  );
}
