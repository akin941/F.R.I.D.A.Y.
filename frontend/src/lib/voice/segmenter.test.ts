import { describe, expect, it } from "vitest";
import {
  CODE_PLACEHOLDER,
  MAX_SENTENCE_CHARS,
  SentenceSegmenter,
  TABLE_PLACEHOLDER,
  speakableLine,
  toSpeakable,
} from "@/lib/voice/segmenter";

function segmentAll(text: string): string[] {
  const s = new SentenceSegmenter();
  return [...s.push(text), ...s.flush()];
}

function segmentStreamed(chunks: string[]): { live: string[][]; all: string[] } {
  const s = new SentenceSegmenter();
  const live = chunks.map((chunk) => s.push(chunk));
  return { live, all: [...live.flat(), ...s.flush()] };
}

describe("speakableLine", () => {
  it.each([
    ["## Başlık burada", "Başlık burada"],
    ["- liste maddesi", "liste maddesi"],
    ["3. üçüncü madde", "üçüncü madde"],
    ["> alıntı", "alıntı"],
    ["Bak: [belgeye](https://example.com/x) git", "Bak: belgeye git"],
    ["![resim](a.png) metin", "metin"],
    ["`npm test` çalıştır", "npm test çalıştır"],
    ["**kalın** ve *eğik* yazı", "kalın ve eğik yazı"],
    ["adres https://example.com/a?b=1 burada", "adres bağlantı burada"],
  ])("%s", (input, expected) => {
    expect(speakableLine(input)).toBe(expected);
  });
});

describe("SentenceSegmenter golden cases", () => {
  it("splits at sentence ends and keeps 20-300 chars", () => {
    expect(segmentAll("Bugün hava çok güzel görünüyor. Yarın yağmur bekleniyor, şemsiye al!")).toEqual([
      "Bugün hava çok güzel görünüyor.",
      "Yarın yağmur bekleniyor, şemsiye al!",
    ]);
  });

  it("never splits after a Turkish abbreviation or an initial", () => {
    expect(
      segmentAll("Dr. Ayşe Yılmaz yarın gelecek. Prof. Ahmet B. Kaya da örn. öğleden sonra uğrar."),
    ).toEqual([
      "Dr. Ayşe Yılmaz yarın gelecek.",
      "Prof. Ahmet B. Kaya da örn. öğleden sonra uğrar.",
    ]);
  });

  it("never splits a decimal number", () => {
    expect(segmentAll("Sıcaklık 3.5 derece olacak ve rüzgar 12.75 km hızla esecek.")).toEqual([
      "Sıcaklık 3.5 derece olacak ve rüzgar 12.75 km hızla esecek.",
    ]);
  });

  it("merges short sentences up to the minimum length", () => {
    expect(segmentAll("Tamam. Oldu. Şimdi bir sonraki adıma geçiyoruz.")).toEqual([
      "Tamam. Oldu. Şimdi bir sonraki adıma geçiyoruz.",
    ]);
  });

  it("splits a sentence over the maximum at a comma or space", () => {
    const long = `${"kelime ".repeat(30)}, ${"başka ".repeat(40)}son.`;
    const out = segmentAll(long);
    expect(out.length).toBeGreaterThan(1);
    for (const sentence of out) expect(sentence.length).toBeLessThanOrEqual(MAX_SENTENCE_CHARS);
    expect(out.join(" ").replace(/\s+/g, " ")).toBe(long.replace(/\s+/g, " ").trim());
  });

  it("replaces a code fence with one placeholder and never reads code", () => {
    const out = segmentAll(
      "İşte örnek komut aşağıda:\n```bash\nrm -rf /tmp/x\necho merhaba\n```\nBunu terminalde çalıştırabilirsin.",
    );
    expect(out.join(" ")).not.toMatch(/rm -rf|echo/);
    expect(out.join(" ")).toContain(CODE_PLACEHOLDER);
    expect(out.at(-1)).toContain("Bunu terminalde çalıştırabilirsin.");
  });

  it("speaks a table as one placeholder", () => {
    const out = segmentAll(
      "Sonuçlar şöyle özetlenebilir:\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\nHepsi bu kadar, teşekkürler.",
    );
    const joined = out.join(" ");
    expect(joined.split(TABLE_PLACEHOLDER)).toHaveLength(2);
    expect(joined).not.toMatch(/\|/);
  });

  it("treats an unclosed fence at the end as code", () => {
    expect(segmentAll("Kodun başlangıcı aşağıda:\n```python\nprint('x')").join(" ")).not.toContain(
      "print",
    );
  });
});

describe("SentenceSegmenter streaming boundaries", () => {
  it("emits the first sentence before the stream ends", () => {
    const { live } = segmentStreamed(["Merhaba, bugün sana ", "yardım edebilirim. Ne ", "istersin?"]);
    expect(live[0]).toEqual([]);
    expect(live[1]).toEqual(["Merhaba, bugün sana yardım edebilirim."]);
  });

  it("does not cut at a period at the very end of a chunk (decimal may follow)", () => {
    const { live, all } = segmentStreamed(["Toplam tutar 3.", "5 milyon lira olarak hesaplandı. "]);
    expect(live[0]).toEqual([]);
    expect(all).toEqual(["Toplam tutar 3.5 milyon lira olarak hesaplandı."]);
  });

  it("holds a half-streamed fence until it closes", () => {
    const s = new SentenceSegmenter();
    // The prose before the fence is complete; the code is held back.
    expect(s.push("Komut şu şekilde olmalı:\n```sh\nmake ")).toEqual(["Komut şu şekilde olmalı:"]);
    const out = [...s.push("test\n```\nSonra sonucu kontrol et lütfen.\n"), ...s.flush()];
    expect(out.join(" ")).not.toContain("make");
    expect(out.join(" ")).toContain(CODE_PLACEHOLDER);
  });

  it("does not split inside a half-streamed link", () => {
    const s = new SentenceSegmenter();
    const first = s.push("Ayrıntılar için [bu belgeye. Bak");
    expect(first).toEqual([]);
    const rest = [...s.push("](https://x.dev) göz atabilirsin. "), ...s.flush()];
    expect(rest.join(" ")).toContain("bu belgeye. Bak göz atabilirsin.");
    expect(rest.join(" ")).not.toContain("https");
  });

  it("streamed and whole input give the same sentences", () => {
    const text =
      "Dr. Kaya yarın saat 10'da gelecek. Toplantı 2.5 saat sürer!\n- Gündem: bütçe\n- Ardından: [plan](http://p) gözden geçirilecek.";
    const chunks = text.match(/[^]{1,7}/g) ?? [];
    expect(segmentStreamed(chunks).all).toEqual(segmentAll(text));
  });

  it("toSpeakable joins everything", () => {
    expect(toSpeakable("**Selam** dünya, nasılsın bugün?")).toBe("Selam dünya, nasılsın bugün?");
  });
});
