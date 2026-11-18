// Kelime düzeyinde fark (LCS). Risk paragraflarının eski/yeni sürümlerini vurgulamak için.

export type DiffOp = { type: "same" | "add" | "del"; text: string };

/** Metni kelimelere ve aradaki boşluklara ayırır; boşluklar önceki kelimeye eklenir. */
function tokenize(text: string): string[] {
  const tokens = text.match(/\S+\s*/g);
  return tokens ?? [];
}

function normalize(token: string): string {
  return token.trim().toLowerCase().replace(/[.,;:!?()"“”]+$/g, "");
}

/** En uzun ortak alt dizi tablosu; çok uzun metinlerde (n·m > 250k) fark hesaplanmaz. */
export function wordDiff(oldText: string, newText: string): DiffOp[] | null {
  const a = tokenize(oldText);
  const b = tokenize(newText);
  const n = a.length;
  const m = b.length;
  if (n * m > 250_000) return null;
  const an = a.map(normalize);
  const bn = b.map(normalize);
  // dp[i][j] = a[i:] ve b[j:] için LCS uzunluğu
  const dp: Uint16Array[] = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = an[i] === bn[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops: DiffOp[] = [];
  const push = (type: DiffOp["type"], text: string) => {
    const last = ops[ops.length - 1];
    if (last && last.type === type) last.text += text;
    else ops.push({ type, text });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (an[i] === bn[j]) {
      push("same", b[j]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      push("del", a[i]);
      i++;
    } else {
      push("add", b[j]);
      j++;
    }
  }
  while (i < n) push("del", a[i++]);
  while (j < m) push("add", b[j++]);
  return ops;
}
