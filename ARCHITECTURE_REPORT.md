# Jarvis OS — Mimari Durum Raporu (AI Agent Katmanı Fizibilitesi İçin)

> Note (2026-09-27): historical snapshot from the MVP 3 era (2026-07-07),
> kept for reference only. The current architecture and roadmap are in
> `docs/VISION_JARVIS_2.md` §2 and `docs/MASTER_PLAN.md` §AA (ADR-008).

> Not (2026-07-07): Bu rapor tarihli bir durum tespitidir; son bölümdeki
> "domain-uzmanı agent'lar bir şirket modül paketine girer" varsayımı ADR-006
> ile geçersizleşmiştir (iş yeri ürünleri harici kurulumlar olarak çalışır).

> Tarih: 2026-07-07 · Branch: `feat/mvp3-knowledge-foundation` · Hazırlayan: Claude Code
> Not: Talepte anılan "CRM" ve genel "dosya yönetimi" özellikleri bu projede **mevcut değil**;
> mevcut özellik seti aşağıda koddan doğrulandığı haliyle listelenmiştir.

## 1. Genel Bakış

- **Amaç:** Kişisel-öncelikli "Personal AI Operating System + Workspace Platform".
  Ortak kimlik/hafıza/izin/audit çekirdeği + takılabilir modüller. Şirket
  workspace'leri çekirdek ürün değil, ileride gelecek modül paketleri.
- **Mevcut özellikler (main + aktif branch):** JWT auth, çok kullanıcılı workspace'ler
  (personal/company), SSE tabanlı AI chat, workspace-kapsamlı semantik hafıza
  (onay akışıyla), knowledge/doküman ingest + chunking (RAG'in ilk yarısı, devam ediyor),
  audit log, AI Gateway + maliyet/kullanım kaydı.
- **Yapı:** Monorepo — `backend/` (Django **modüler monolit**, ayrı mikroservis yok),
  `frontend/` (Next.js), `deploy/docker/`, `docs/` (ADR'ler, prompt pack'leri, governance).

## 2. Teknoloji Stack'i

- **Backend:** Python 3.12 + `uv`, Django 5.1, DRF, SimpleJWT, Celery 5.4 (Redis broker),
  `pgvector` 0.3.6, `anthropic` + `openai` SDK'ları (yalnızca gateway adapter'larında),
  structlog, sentry-sdk. Lint: ruff + import-linter (bağımlılık yönü sözleşmeleri).
- **Frontend:** Next.js 16.2 (port 3001), React 19.2, Tailwind 4, shadcn/radix,
  Vitest + Playwright. Backend'e BFF route'ları üzerinden gidiyor.
- **Docker compose servisleri** (`deploy/docker/compose.yml`, dev amaçlı):
  `web` (Django runserver, host :8001), `worker` (Celery + gömülü beat — tek replika
  varsayımı), `postgres` (**pgvector/pgvector:pg16 imajı — pgvector zaten kurulu**),
  `redis:7` (broker + cache), `minio` (S3-uyumlu obje deposu; compose ve settings'te
  tanımlı ancak **uygulama kodunda henüz kullanılmıyor** — dosyalar Postgres'te
  `raw_text` olarak tutuluyor).
- Dev stack varsayılanı deterministik **fake LLM provider** (`AI_FAKE_PROVIDER=true`).

## 3. Veri Modeli Özeti

- **Workspace veri ekseni (ADR-001):** Her iş tablosu `workspace_id` taşır; tüm sorgular
  `core/db.py`'deki `for_actor()` kapısından geçer. Kişisel/şirket verisi karışamaz.
- **Ana entity'ler:** `User` (custom, e-posta tabanlı) → `WorkspaceMember`
  (rol: owner/admin/member/viewer) → `Workspace`. `Conversation`/`Message` (chat),
  `Memory` (semantic/episodic/preference/procedural; `pending_approval→approved/rejected`
  durum makinesi) + `MemoryExtractionJob` (lease-fenced async çıkarım),
  `KnowledgeDocument`/`KnowledgeChunk` (pgvector `VectorField`, sabit boyutsuz;
  provider başına dimension eşleme sorumluluğu retrieval'da), `AuditLog`,
  `ModelProvider`/`PromptTemplate`/`PromptVersion`/`ModelUsage`.
- **Auth/yetki:** JWT (SimpleJWT); rol × endpoint izin testleri her API yüzeyinde zorunlu
  (CLAUDE.md kural 7). Rol denetimi servis katmanında (`get_role` + `_WRITER_ROLES` gibi).
- **Hassasiyet modeli:** `private` işaretli Memory/KnowledgeDocument içerikleri hiçbir
  gateway payload'ına giremez (çift taraflı "cloud-eligibility" negatif sözleşmesi).

## 4. Mevcut AI Özellikleri

- **Tek kapı — InferenceGateway (`ai_core/gateway.py`, ADR-003):** Tüm LLM çağrıları
  buradan; provider SDK'ları yalnızca `ai_core/adapters/` içinde (anthropic,
  openai_compat, fake). Retry + provider fallback, timeout, maliyet hesabı dahili.
- **ModelUsage:** Her çağrı (başarı/hata/stream kopması dahil) tam **1** satır yazar:
  token, maliyet, gecikme, purpose, workspace, prompt versiyonu, context raporu.
- **Chat:** `assistant` app'i; DRF view → `StreamingHttpResponse` ile **SSE streaming**
  (WebSocket yok). Hafıza bağlamı `build_context` ile injection-safe çerçevede eklenir.
- **Embedding:** `InferenceGateway.embed()` main'de mevcut (MVP 3 branch 1);
  provider-agnostik, varsayılan OpenAI `text-embedding-3-small`; fake adapter'ın
  deterministik bag-of-words embedding'i test için hazır.
- **Prompt'lar:** Runtime'da DB'de (`PromptTemplate`/`PromptVersion`); tasarım/master
  metinleri `docs/prompts/` altında. **Async:** Celery worker (hafıza çıkarımı çalışıyor;
  embedding worker'ı branch 3'te gelecek).

## 5. Entegrasyon Noktaları (Agent Katmanı Açısından)

- **Agent-service nereye bağlanır:** Mimari kararlar gereği ayrı bir mikroservis değil,
  monolit içinde yeni bir **kernel app** (ör. `backend/agents/`) olarak eklenmeli —
  `memory`/`knowledge` app'lerinin izlediği şablon (models + services + views +
  import-linter sözleşmesi + rol testleri) birebir uygulanabilir. Uzun süren agent
  adımları için Celery altyapısı hazır (lease-fenced job deseni `memory/jobs.py`'de
  kanıtlanmış). MASTER_PLAN'da "Agent Runtime" (§G) bölümü ayrılmış ama boş.
- **Gerçek zamanlı kanal:** WebSocket altyapısı **yok**; chat SSE ile çalışıyor.
  Agent-chat için mevcut SSE deseni yeniden kullanılabilir; çift yönlü/çok turlu
  etkileşim gerekirse Django Channels eklemek yeni bir mimari karar (ADR) gerektirir.
- **pgvector:** Engel yok — imaj `pgvector/pgvector:pg16`, `vector` extension migration'ı
  ve `VectorField` kolonları zaten var. RAG retrieval (cosine, `<=>`) branch 4'te planlı;
  `cloud_eligible()` + provider-dimension filtresi sözleşmeleri tanımlı.
- **Doküman ingest → RAG:** Evet, `knowledge` app'i tam olarak bu amaçla yapılıyor.
  Şu an yalnızca UTF-8 düz metin/markdown (max ~1 MB) + pasted text; URL kaynağı ve
  async embedding worker branch 3'te. PDF/Office parse **kapsam dışı** — agent katmanı
  bunlara ihtiyaç duyarsa ayrı iş kalemi (MinIO bu noktada devreye alınabilir).
- **MCP:** Kod düzeyinde entegrasyon yok; ancak governance süreci hazır
  (`docs/security/mcp-governance.md`: allowlist + manifest zorunluluğu).

## 6. Mevcut Plan / Roadmap

- **Roadmap (MASTER_PLAN §L):** MVP 0–20. Kişisel çekirdek sırası:
  shell/chat → memory → **knowledge/RAG (şu an)** → tasks → tools/approvals →
  router → **agents** → skills → integrations. Şirket workspace'leri MVP 10+,
  doküman-zekâsı (document intelligence) paketi MVP 11–15.
- **Tamamlanan:** MVP 0 (iskelet), MVP 1 (auth, workspace, gateway v0, SSE chat,
  Next.js), MVP 2 (memory + onay akışı + çıkarım pipeline'ı) — main'de.
- **Devam eden — MVP 3 (6 branch):** 1) embedding gateway ✅ main'de,
  2) knowledge app ✅ (bu branch, review turları tamamlanmış görünüyor),
  3) ingestion worker + URL/SSRF sertleştirme, 4) RAG context injection,
  5) Knowledge UI, 6) e2e + docs — bekliyor.
- **Bağlayıcı kararlar:** ADR-001 workspace ekseni, ADR-002 modüler monolit
  (kernel ↔ modül tek yönlü bağımlılık), ADR-003 gateway tek kapı, ADR-004 onay
  kapısı kuralları, ADR-005 hafıza yönetişimi (Obsidian yalnızca mirror).
- Geliştirme süreci: her branch Codex CLI review'ından APPROVE alana kadar iterasyon.

## 7. Riskler ve Kısıtlar

**Mimari kısıtlar (agent katmanını şekillendirir, çoğu kasıtlı):**
- ADR-004: yazma/silme/deploy/dışa-gönderme **onay kapısız yapılamaz**; agent'lar kendi
  tanım/izinlerini değiştiremez; coder ve reviewer agent aynı olamaz. Talep edilen
  "insan onay kuyruğu" bu kuralla birebir örtüşüyor — ancak **onay kapısının kendisi
  henüz kodda yok** (roadmap'te MVP "tools/approvals" adımı, agents'tan önce).
  Agent katmanı bu adıma bağımlı.
- Her agent LLM adımı gateway + ModelUsage'dan geçmek zorunda; framework seçimi
  (LangGraph vb.) bu kısıtla uyumlu olmalı — provider SDK'sını doğrudan çağıran
  framework'ler ADR-003'ü ihlal eder.
- MCP araçları allowlist + manifest süreci olmadan kurulamayacak.

**Teknik borç / boşluklar:**
- RAG zinciri yarım: retrieval, embedding worker'ı ve UI yok (branch 3–6 bekliyor).
  Agent katmanı RAG'e dayanacaksa MVP 3'ün bitmesi ön koşul.
- WebSocket yok; MinIO bağlanmış ama kullanılmıyor; binary doküman (PDF vb.) parse yok.
- Celery beat tek-worker varsayımıyla gömülü — worker ölçeklenirse beat ayrılmalı.
- Roadmap'te agents'tan önce "tasks" ve "router" adımları var; agent katmanını öne
  çekmek bu iki bağımlılığı da öne çeker (belirsiz: router'ın kapsamı docs'ta detaylandırılmamış).

**Önerim ve endişelerim (Claude Code):**
- Fizibilite **yüksek**: talep edilen katmanın yapı taşlarının çoğu (workspace scoping,
  gateway, ModelUsage, Celery job deseni, pgvector, hafıza, audit) hazır ve agent
  senaryosu düşünülerek tasarlanmış. Doğru yol ayrı servis değil, `agents` kernel app'i.
- Sıralama endişesi: RAG (MVP 3) ve onay kapısı (tools/approvals) bitmeden domain-uzmanı
  agent'lara başlamak, ADR-004'ü sonradan yamamak anlamına gelir — önce bu iki temel.
- Kullanıcı-bazlı semantik hafıza büyük ölçüde mevcut (workspace-kapsamlı Memory);
  "kullanıcı bazlı, workspace'ler-arası" bir hafıza isteniyorsa bu ADR-001/005'e dokunan
  yeni bir karar gerektirir (Open Decisions'taki cross-workspace overlay notuna bakınız).
- Domain-uzmanı agent'lar muhtemelen bir şirket modül paketi kapsamına girer;
  kernel'e değil `apps.modules.*` tarafına konmalı ki tek yönlü bağımlılık kuralı korunsun.
