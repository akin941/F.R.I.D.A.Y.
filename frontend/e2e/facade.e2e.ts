import { expect, test } from "@playwright/test";
import { mintFacadeToken } from "./manage";

// Phase 4 closeout: a smoke of the OpenAI-compatible facade through the BFF
// (`/v1/*` → Django `/api/v1/openai/*`), the path an Open WebUI client
// takes. Uses Playwright's request context only -- no browser session, no
// cookies: the facade authenticates by its own `openai`-kind token alone.
test("OpenAI-compatible facade: models, completion, streaming, token kind", async ({
  request,
}) => {
  const token = mintFacadeToken();
  const auth = { Authorization: `Bearer ${token}` };

  const models = await request.get("/v1/models", { headers: auth });
  expect(models.status()).toBe(200);
  const ids = ((await models.json()).data as { id: string }[]).map((m) => m.id);
  expect(ids).toEqual(expect.arrayContaining(["jarvis", "jarvis-lite"]));

  const completion = await request.post("/v1/chat/completions", {
    headers: auth,
    data: { model: "jarvis", messages: [{ role: "user", content: "Merhaba Jarvis" }] },
  });
  expect(completion.status()).toBe(200);
  const body = await completion.json();
  expect(body.object).toBe("chat.completion");
  expect(body.choices[0].finish_reason).toBe("stop");
  expect(body.choices[0].message.content.length).toBeGreaterThan(0);

  const stream = await request.post("/v1/chat/completions", {
    headers: auth,
    data: {
      model: "jarvis-lite",
      stream: true,
      messages: [{ role: "user", content: "Kısa bir selam" }],
    },
  });
  expect(stream.status()).toBe(200);
  expect(stream.headers()["content-type"]).toContain("text/event-stream");
  const text = await stream.text();
  expect(text).toContain("chat.completion.chunk");
  expect(text.trim().endsWith("data: [DONE]")).toBe(true);

  // Low-trust by construction: no token → 401 at the BFF, and tools are
  // refused rather than silently ignored.
  const anonymous = await request.get("/v1/models");
  expect(anonymous.status()).toBe(401);
  const withTools = await request.post("/v1/chat/completions", {
    headers: auth,
    data: {
      model: "jarvis",
      messages: [{ role: "user", content: "x" }],
      tools: [{ type: "function", function: { name: "f", parameters: {} } }],
    },
  });
  expect(withTools.status()).toBe(400);
});
