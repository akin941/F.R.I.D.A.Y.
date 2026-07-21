# Jarvis 2.0 — Kişisel + Ajans İşletim Sistemi (Vizyon Mimarisi ve Yol Haritası)

> **Durum: ONAYLANDI (2026-07-17)** — bağlayıcı hali **ADR-008** +
> MASTER_PLAN §AA'dır. Onayla birlikte dört ek gereksinim işlendi:
> tek-efendi sistemi (ADR-008 k.2), lokal wake-word'lü sürekli dinleme
> (Faz 6), Home Assistant köprüsü (Faz 5), görsel kimlik katmanı (Faz 6
> sonrası). Analiz, tüm ADR'ler (001–007), MASTER_PLAN ve kod envanteri
> (backend ~13.200 satır kod + ~16.900 satır test, 15 kernel app;
> frontend ~8.100 satır) okunarak yapılmıştır.

---

## 0. Vizyonun özeti

Jarvis, kullanıcının hayatının merkezinde duran gerçek bir asistana dönüşür:

1. **Çoklu-firma yönetimi** — her firma bir workspace: kendi bağlamı,
   repoları, servisleri, geçmişi. "ADİA'ya en son ne yaptık?" cevaplanabilir.
2. **Agent Harness (işin kalbi)** — CLI agent'ları (Claude Code, Codex CLI,
   Gemini CLI, Ollama yerel modeller) **işçi** olarak kullanılır:
   istek → brifing → onay → izole worktree'de implementasyon → test →
   deploy onayı (ikinci kapı) → VPS'te deploy.
3. **Semantik hafıza** — kullanıcıyı VE firmaları tanıyan uzun vadeli hafıza;
   her konuşmada bağlam tekrarı gerekmez.
4. **UI + Ses** — mevcut UI yenilenir (hazır arayüzler değerlendirildi,
   aşağıda); ses yol haritasında kalır ama ilk fazda değildir.
5. **Entegrasyonlar (MCP)** — GitHub, VPS/deploy, external installation (MCP sunucusu
   olduğunda), sosyal medya (mevcut servislere bağlanarak), mail/takvim.

Pazarlıksız kısıtlar: ADR-004 onay kapıları, self-mod koruması, scope
disiplini her mimaride korunur; deploy otomasyonunda **çift onay**; olgun
araç varken sıfırdan yazılmaz; her faz sonunda **kullanılabilir** bir şey.

**Tek-efendi sistemi (ADR-008 k.2, onayla eklendi):** Jarvis yalnız
sahibine hizmet eder. Çok-kullanıcılı SaaS deseni (mail ile kayıt, giriş
formu, davet/üyelik akışları) kaldırılır; kimlik = cihaz güveni (lokal
ağda cihaz token'ı/PIN, dışarıdan tek güçlü anahtar) — Faz 4'te login
akışının yerine gelir. ADR-004'ün istekçi≠karar-verici (iki insan) kuralı
tek insanla tanım gereği imkânsızdır; yerine geçen kural: her kapılı
eylem, isteği üreten eylemden **ayrı, açık, audit'li bir sahip kararı**
gerektirir; makine aktörleri asla karar veremez; yazan≠review-eden kuralı
harness'te çapraz-vendor olarak korunur. (Bu analizin ortaya çıkardığı
gerçek defekt: bugün üretimde agent'ın açtığı ApprovalRequest
`requested_by=sahip` taşıdığı için tek kullanıcılı kurulumda hiç
onaylanamıyor — Faz 0'da düzeltilir.)

---

## 1. Ana mimari karar: A / B / C analizi

### Seçenek A — Jarvis çekirdek kalır; Open WebUI ön yüz; harness yan servis

**Ne korunur:** Backend'in tamamı. **Ne atılır:** Next.js frontend'in çoğu.

**Değerlendirme:** Open WebUI (ve LibreChat) OpenAI-uyumlu bir chat API'sine
bağlanmak üzere tasarlanmış **chat-first** ürünlerdir. Jarvis'in günlük
değerinin kalbi olan akışlar — onay kutusu (ApprovalRequest kararı), task
draft confirm kartları, agent adım görünürlüğü + kill switch, harness
brifing/plan/deploy onayları — bu ürünlerin veri modelinde yoktur. İki çıkış
var, ikisi de kötü:

- Bu akışları Open WebUI eklentileriyle (Pipes/Functions) taklit etmek =
  olgun aracı kullanmak değil, olgun aracın **içine** amatör UI yazmak.
  "Hazır varken yazma" kısıtına ruhen aykırı.
- Akışları düz metne indirmek (Telegram kanalının yaptığı gibi) = MVP 9'da
  bilinçli olarak *ikincil kanal* için kabul edilen kısıtlamayı *birincil
  arayüz* yapmak. ADR-004'ün UX'i (kim istedi, ne yapacak, reddet/onayla)
  metin komutuna düşer — güvenlik omurgasının kullanılabilirliği zayıflar.

Ayrıca kritik bir kural çelişkisi: ADR-004 kararları **yüksek-güven kanal**
gerektirir (MVP 9 kanal-güven modeli: web login dışındaki her kanal düşük
güven, ApprovalRequest **karar veremez**). Open WebUI, Jarvis'e API ile
bağlanan bir istemci olarak tanımı gereği düşük-güven kanaldır → onaylar
yine ayrı bir Jarvis UI'sinde verilmek zorunda kalır → "tek arayüz" vaadi
zaten çöker.

**Sonuç: birincil arayüz olarak reddedilir.** Ama tamamen çöpe de gitmez —
bkz. Seçenek C'deki "OpenAI-uyumlu cephe" notu.

### Seçenek B — Harness-merkezli yeni omurga; güvenlik+hafıza içine taşınır

**Değerlendirme:** Bu bir yeniden yazımdır. Taşınacak denen modüller
(approvals, memory, audit, workspaces, gateway) zaten birbirine `for_actor()`,
kanal-güven, audit ve import-linter sözleşmeleriyle bağlı — "modülü taşı"
pratikte "yeniden yaz + yeniden test et" demektir (~13k satır kod, ~17k satır
test, MVP 7-9'da toplam 20+ Codex review turuyla kapatılmış gerçek güvenlik
bug'ları: iki-transaction atomicity açığı, kayıp on_commit, PAT allowlist
P1'leri...). Harness ise mimari olarak omurga değil **işçi katmanı**dır:
uzun süren, izole, dışarıda çalışan job'lar. Omurgayı işçiye göre kurmak,
arabayı bagaja göre tasarlamaktır. Kazanım yok, risk devasa.

**Sonuç: reddedilir.**

### Seçenek C (ÖNERİ) — Jarvis = kontrol düzlemi; harness = yeni kernel app; UI = kendi arayüzünde odaklı yenileme

Üçüncü yol, mevcut mimarinin zaten işaret ettiği yerdir:

- **Jarvis çekirdeği kontrol düzlemi (control plane) olarak kalır:** kimlik,
  workspace ekseni (ADR-001), gateway (ADR-003), onay kapısı (ADR-004),
  hafıza (ADR-005), audit. Bunlar tam da harness'in ihtiyaç duyduğu şeyler —
  ADR-003 bugünkü haliyle bile "**external CLI backends' usage reports**"
  ifadesini içeriyor; bu vizyon ADR'nin öngördüğü geleceğin ta kendisi.
- **Harness yeni bir kernel app olur** (`harness`), modüler monolit içinde
  (ADR-002): `approvals` + `tools` + `audit`'i import eder, dış dünyada
  CLI agent'ları **headless** çalıştırır (Claude Code `-p`/Agent SDK,
  `codex exec`, Gemini CLI, Ollama). Uzun işler mevcut lease-fenced Celery
  job desenine (memory/knowledge/vault-export'ta 3 kez kanıtlandı) oturur.
- **UI kendi Next.js kabuğunda yenilenir** (ayrı faz): BFF, auth, SSE,
  approvals/agents bileşen altyapısı korunur; kabuk + chat + yeni "harness
  konsolu" profesyonel bir tasarım geçişinden geçer. Buna ek olarak ucuz bir
  **OpenAI-uyumlu cephe endpoint'i** (`/v1/chat/completions` → mevcut
  assistant pipeline) açılabilir: Open WebUI/LibreChat/başka herhangi bir
  istemci **ikincil, salt-chat kanalı** olarak bağlanabilir (MVP 9'un kanal
  deseninin birebir tekrarı: düşük güven, onay kararı veremez). Böylece
  "olgun aracı kullan" isteği, güvenlik modelini bozmadan karşılanır.

**Neden C kazanır:** A'nın "hazır UI" kazanımını cephe endpoint'i ile kısmen
alır, B'nin hiçbir riskini almaz, ve 9 MVP'lik test edilmiş güvenlik
omurgasının tamamını harness'in altına serer. Harness'e verilecek yetkiler
(repo yazma, deploy) bu codebase'in şimdiye kadarki en riskli yetkileri —
bunları taşımak için kanıtlanmış onay/audit altyapısından daha iyi bir yer
yok.

---

## 2. Mevcut koddan ne taşınır — modül envanteri

| Modül (kod/test satırı) | Karar | Not |
|---|---|---|
| `core` (198/127) | **KORU** | `for_actor`, kanal-güven — harness'in temeli |
| `accounts` (488/439) | **UYARLA (sadeleşir)** | Tek-efendi kararı: signup kapatılır/kaldırılır, davet/üyelik asla gelmez; PAT altyapısı kalır (cephe + CLI kanalı bunu kullanır); Faz 4'te login yerine cihaz-güveni (LAN token/PIN + dış güçlü anahtar) |
| `workspaces` (165/82) | **UYARLA** | Firma profili eklenir: repolar, servisler, deploy hedefleri, iletişim (bkz. Faz 1). Multi-user tenancy (MVP 10) **iptal — ADR-008 tek-efendi**; üyelik/davet hiçbir zaman gelmez, firmalar sahibin firma profilleri |
| `audit` (141/144) | **KORU** | Harness'in her adımı buraya yazar |
| `ai_core` (1982/1217) | **UYARLA** | STT/TTS capability'leri yeni eklendi (4f34da2). Eklenecek: model-tier/maliyet yönlendirme tablosu (§J'deki ertelenmiş alt görev artık yük taşıyıcı), CLI agent kullanım raporlarının ModelUsage'a yazılması, Ollama embedding provider |
| `assistant` (1193/2957) | **UYARLA** | Chat pipeline + intent router korunur; router'a `harness_request` intent'i eklenir; OpenAI-uyumlu cephe bunun üstüne oturur |
| `memory` (1505/2368) | **UYARLA (büyük)** | Bkz. §3 hafıza analizi — embedding yok, episodik otomatik kayıt yok, cross-workspace overlay yok |
| `knowledge` (1887/2813) | **KORU** | Firma dosyaları/RAG olduğu gibi kullanılır (workspace-scoped zaten) |
| `tasks` (616/657) | **KORU** | |
| `approvals` (645/995) | **KORU — kalp** | Harness'in iki onay kapısı (plan + deploy) birebir bu makineyi kullanır, yeniden yazılmaz |
| `tools` (1027/898) | **UYARLA** | ToolSpec registry'ye harness araçları eklenir (`harness.run`, `harness.deploy`) — "ikinci tool gelince registry'yi genelleştir" notunun vakti geldi |
| `agents` (1855/2818) | **KORU (dar rol)** | MVP 7 runtime'ı *chat-doğumlu hafif planlar* için kalır (4 fren + kill switch değerli). Harness ayrı app olur; ikisini birleştirmek Faz 3 sonrası değerlendirilir. Agents'ı harness'e zorla dönüştürmek YAPILMAZ |
| `skills` (304/429) | **KORU** | Tekrarlayan harness iş akışları ileride skill şablonu olur (git-onaylı, DB'siz — desen hazır) |
| `integrations` (788/1052) | **KORU** | Telegram + CLI kanalları; cephe endpoint'i üçüncü kanal olarak eklenir |
| Frontend kabuk/BFF/auth/SSE (~8.1k) | **KORU altyapı** | BFF deseni, `sse.ts`, api route'ları kalır |
| Frontend görünümler (chat, shell, sayfalar) | **UYARLA/YENİLE** | "Amatör ve buglı" olan katman bu — Faz 4'te tasarım sistemi + bileşen yenileme; sıfırdan framework değişikliği YOK |
| `voice` (JarvisOrb, VoiceView) + STT/TTS gateway temeli | **KORU (uykuda)** | MVP 12 dalları Faz 6'ya kayar; yazılan temel (Capability.STT/TTS, transcribe/synthesize, orb) durur, atılmaz |

**Atılan:** pratikte hiçbir backend modülü atılmıyor. Atılan tek şey
frontend'in *görsel katmanı* ve "voice'un sıradaki MVP olması" varsayımı
(ADR-007'nin sıralaması — aşağıda).

---

## 3. Semantik hafıza — mevcut durum ve eksikler

**Bugün elimizde olan (güçlü ve korunmalı):** workspace-scoped, onay
akışlı (pending→approved) küratörlü hafıza; async çıkarım (lease-fenced
Celery); injection-safe bağlam enjeksiyonu; `private` asla buluta gitmez;
her erişim audit'li; Obsidian aynası. Bu kürasyon disiplini Letta/MemGPT'de
bile yok — **atılmaz**.

**Eksikler (vizyonun istediği "beni ve firmaları tanısın" için):**

1. **Memory embeddings yok.** Semantik arama sadece knowledge'da var
   (MASTER_PLAN §V, madde 2 bunu açıkça söylüyor). Hafıza bugün "hepsini
   enjekte et" bütçesiyle çalışıyor; ölçeklenmez. → `Memory` satırlarına
   pgvector embedding + retrieval (knowledge'daki kanıtlanmış desenin
   kopyası, aynı gateway `embed()` yolu).
2. **Episodik iş günlüğü otomatik yazılmıyor.** "ADİA'ya en son ne yaptık?"
   sorusunun cevabı, harness run'larının ve önemli konuşma sonuçlarının
   *otomatik* episodik kayda düşmesini gerektirir. Mevcut kürasyon kuralı
   (her AI önerisi onay bekler) iş günlüğü için fazla ağır → yeni, düşük
   riskli `work_log` hafıza sınıfı: **otomatik onaylı ama silinebilir/audit'li**
   (ADR-005'e dar istisna, ADR-008 k.4). Otomatik onay yalnız
   **sistem-üretimi etkinlik özetlerini** kapsar (harness run'ları, kapılı
   araç yürütmeleri, task/deploy sonuçları); konuşma-türevi adaylar,
   private-conversation-flag kararı (§O) Faz 1'de çözülene dek
   `pending_approval` kalır; sensitivity alanı ve "private asla gateway
   payload'ına girmez" negatif sözleşmesi aynen geçerli.
3. **Cross-workspace kişisel katman çözülmemiş** (§O'da açık karar).
   "Asistan hepsini tanır" tam da bu: kişisel asistan görünümünün firma
   workspace'lerine **salt-okunur + audit'li** bakabilmesi. ADR-001
   izolasyonuna dokunan en hassas karar; Faz 1'in tasarım işi.
4. **Konsolidasyon/damıtma yok** (ADR-005 "gerekli olacak" demişti).
   Letta/MemGPT'den alınacak **desenler**: çekirdek "memory block"lar
   (kim/tercihler/firma özetleri — her bağlama giren kısa, kendini
   güncelleyen özetler) + arşiv katmanı (embedding'li satırlar). Framework
   alınmaz (Postgres source of truth — ADR-005), desen alınır.

---

## 4. Agent Harness — tasarım çerçevesi

Akış (kısıtlardaki çift onay dahil):

```
istek (chat/UI) ─→ HarnessJob (workspace + repo + hedef)
  → [1] KEŞİF: CLI agent salt-okunur (plan modu) → BRİFİNG kullanıcıya
  → [2] ONAY KAPISI #1 (ApprovalRequest, mevcut makine)
  → [3] İMPLEMENTASYON: izole git worktree, dal push (asla main)
  → [4] TEST + ÇAPRAZ REVIEW: yazan agent ≠ review eden agent
        (ADR-004 "coder ≠ reviewer" kuralı çapraz-vendor uygulanır:
        Claude yazdıysa Codex review eder — MVP 7-9'da fiilen kanıtlanmış pratik)
  → [5] ONAY KAPISI #2 (deploy onayı, ayrı ApprovalRequest)
  → [6] DEPLOY: VPS'te pull + build + restart (allowlist'li komut seti)
  → [7] episodik work_log kaydı + ModelUsage raporu
```

**İş dağıtımı (çoklu-model):** harness seviyesinde politika tablosu —
- **Claude Code**: ağır implementasyon, mimari işler, riskli değişiklikler
- **Codex CLI**: alternatif implementör + birincil çapraz-reviewer
- **Gemini CLI**: yedek/karşılaştırma
- **Ollama (yerel)**: brifing özetleme, commit mesajı taslağı, sınıflandırma,
  embedding — ücretsiz/özel işler
Gateway'in ertelenmiş model-tier yönlendirme alt görevi (§J) bununla
birleşir: iç gateway çağrıları tier tablosundan, harness işleri politika
tablosundan yönlendirilir; ikisi de `ModelUsage.routing_reason`a yazar.

**Güvenlik (pazarlıksız):** harness job'ları kendi tanımlarını/izinlerini
değiştiremez (ADR-004 self-mod); worktree dışına yazamaz; komut allowlist'i;
secrets yalnız env; repo içeriğinden gelen talimatlar (prompt injection)
düşük-güven veri kabul edilir; her adım audit + kill switch + bütçe tavanı
(MVP 7'nin 4 freninin harness'e uyarlanması).

**Çalışma yeri (açık soru O-1):** CLI agent'lar nerede koşar? Geliştirme
Mac'i (Ollama zaten orada, repo erişimi kolay) vs VPS (7/24 ama API maliyeti
ve repo anahtarları oraya taşınır). Muhtemel cevap: runner soyutlaması —
Celery worker'ın koştuğu her makine runner olabilir; ilk sürüm Mac.

---

## 5. UI kararı (özet)

- **Birincil arayüz:** mevcut Next.js kabuğu, Faz 4'te odaklı yenileme
  (tasarım sistemi, kabuk, chat, harness konsolu, onay gelen kutusu).
  Gerekçe: onay kapısı + brifing + deploy UX'i hiçbir hazır chat ürününde
  yok; kanal-güven kuralı gereği onaylar zaten yüksek-güven kanalda kalmalı.
- **İkincil kanal:** OpenAI-uyumlu cephe endpoint'i → Open WebUI/LibreChat
  isteyen istemci bağlanır (salt chat, düşük güven, onay veremez). Ucuz,
  MVP 9 deseninin tekrarı, "olgun aracı kullan" isteğinin güvenli karşılığı.
- **Ses:** Faz 6'ya kadar uykuda; yazılmış temel korunur.

---

## 6. Fazlı yol haritası (her faz sonunda kullanılabilir değer)

| Faz | İçerik | Faz sonunda elde ne var? |
|---|---|---|
| **0. Karar + zemin** (kısa) | ADR-008 (bağlayıcı: tek-efendi, harness kernel app, work_log sınıfı, voice yeniden sıralama, cephe kanalı); approvals tek-efendi karar kuralı düzeltmesi (agent istekleri üretimde onaylanabilir olur) + SIGNUP kapalı; firma workspace profil alanları; Ollama embedding provider; model-tier iskeleti — brief: `docs/prompts/phase-0-foundation/brief.md` | Yerel LLM ile uçtan uca günlük kullanım; firmalar tanımlı; agent onayları çalışır |
| **1. Hafıza v2** | Memory embeddings + semantik retrieval; `work_log` sınıfı; çekirdek "memory block" özetleri (kişi + firma dosyası); cross-workspace salt-okunur kişisel katman (§O kararı çözülür) | "ADİA'ya en son ne yaptık?" cevaplanır; bağlam tekrarı biter |
| **2. Harness v1 — keşif/brifing** | `harness` app; workspace'e repo kaydı; CLI agent'ı salt-okunur headless çalıştır → brifing üret; ModelUsage raporu; kill switch + bütçe; iş dağıtım politikası v0 | "X reposunda şu bug'ı analiz et, brifing ver" çalışır — yazma yok, risk düşük |
| **3. Harness v2 — implementasyon** | Onay kapısı #1 → worktree'de implementasyon → test → çapraz review → dal push (gated) | "X firmasına şu özelliği ekle" → onaylı, test edilmiş, review'lu dal |
| **4. UI yenileme** | Kabuk + chat + harness konsolu + onay gelen kutusu redesign; kayıt/login yerine **cihaz-güveni kimliği** (LAN token/PIN + dış güçlü anahtar, signup kodu kaldırılır); OpenAI-uyumlu cephe endpoint'i | Her gün kullanılan profesyonel arayüz; istenirse Open WebUI ikincil istemci |
| **5. Deploy + MCP** | Onay kapısı #2 + VPS deploy (allowlist); GitHub MCP; mail/takvim MCP; external MCP istemcisi (panel sunucu olduğunda); **Home Assistant köprüsü** (cihaz sürücüsü yazılmaz — lamba/klima/Wake-on-LAN HA üzerinden, onay kapısı arkasında); sosyal medya mevcut servis bağlantıları | Uçtan uca: istek → brifing → onay → kod → onay → canlıda; ev/cihaz kontrolü |
| **6. Ses (eski MVP 12)** | Mic capture, faster-whisper STT, Piper TTS, **lokal wake-word** ("Hey Jarvis" — openWakeWord/Porcupine sınıfı, ses cihazdan çıkmaz, komut ancak wake-word sonrası işlenir), orb'un gerçek sese bağlanması | Sürekli dinleyen, tamamen lokal sesli Jarvis |
| **6+. Görsel kimlik** | Animasyonlu, konuşan Jarvis arayüz katmanı (uykudaki orb temeli üzerine) — bilinçli olarak EN SON: altyapı çalışmadan görsel kabuk boş kostümdür | Jarvis-benzeri görsel deneyim |

Sıralama gerekçesi kullanıcının verdiği değer sırasıdır: önce
hafıza+harness, sonra UI, en son ses. Hafıza harness'ten önce gelir çünkü
harness run'larının episodik kayıt düşeceği yer önce var olmalı.

**ADR-007 notu:** ADR-007 voice'u "sıradaki iş" yapmıştı; bu vizyon onu
Faz 6'ya iter. Bu bir roadmap değişikliğidir → onayla birlikte ADR-008'de
açıkça kayda geçer (ADR-007 iptal edilmez; teslim ettiği temel duruyor,
yalnızca sırası değişiyor).

---

## 7. Faz 1 için somut kapsam önerisi (onay sonrası ilk brief)

`docs/prompts/phase-1-memory-v2/brief.md` olarak yazılacak; dallar:

1. **memory-embeddings:** `Memory.embedding` (pgvector) + geriye dönük
   doldurma job'ı (lease-fenced, knowledge deseninin kopyası) + retrieval'ın
   `build_context`e bağlanması (mevcut "hepsini enjekte" bütçesinin yerine
   benzerlik + tazelik karışımı).
2. **work-log sınıfı:** `Memory.memory_type`/source genişletmesi;
   otomatik-onaylı ama audit'li + silinebilir `work_log` — yalnız
   sistem-üretimi etkinlik özetleri (ADR-008 k.4; harness gelince
   run'ları da buraya). Konuşma-türevi kayıt bu dalda **otomatik
   onaylanmaz**: private-conversation-flag kararı (§O) bu fazın tasarım
   işi; çözülene dek konuşma özetleri `pending_approval` doğar.
3. **firma dosyası:** `Workspace` profil alanları (repolar, servisler,
   deploy hedefi, notlar) + firma özeti "memory block" (her bağlama giren
   kısa özet) + `/workspaces` UI'sinde dosya görünümü.
4. **kişisel katman:** cross-workspace salt-okunur overlay tasarım kararı +
   dar implementasyon (kişisel asistan sorusu firma work_log'larını
   okuyabilir; her okuma audit'li; `private` asla). §O kararı kapanır.

Test stratejisi mevcut disiplinin aynısı: rol × endpoint izin testleri,
FakeAdapter ile gateway mock'u, izolasyon testleri (kişisel katmanın
sızdırmadığının kanıtı release gate).

---

## 8. Riskler ve açık sorular

**Riskler**
- **R1 — Harness yetki riski:** repo yazma + deploy, codebase'in en riskli
  yetkileri. Panzehir: fazlara bölünmüş yetki (v1 salt-okunur), çift onay,
  worktree izolasyonu, komut allowlist, çapraz review, kill switch, bütçe.
- **R2 — Prompt injection:** firma repolarının içeriği (README, kod
  yorumları, issue'lar) düşük-güven girdidir; harness brifingi "repo böyle
  diyor" ile "ben bunu öneriyorum"u ayırmalı; onay metni kullanıcıya ham
  repo talimatı değil, harness'in kendi planını gösterir.
- **R3 — Maliyet:** harness run'ları uzun ve pahalı olabilir. ModelUsage
  zaten her çağrıyı yazıyor; run başına bütçe tavanı + tier yönlendirme şart.
- **R4 — Uzun job'lar:** 10-60 dk'lık run'lar Celery'de tutulacak; log
  akışı UI'ye SSE ile taşınmalı (chat SSE altyapısı örnek).
- **R5 — Hafıza kirliliği:** otomatik work_log kürasyonsuz büyür; silinebilir
  + audit'li + konsolidasyon (damıtma) Faz 1'de birlikte gelmeli.
- **R6 — Kapsam sürüklenmesi:** 6 faz büyük; MVP disiplini (dal tablosu,
  brief, her faz kapanışı) aynen devam eder.

**Açık sorular — onayla güncellendi (2026-07-17)**
- ~~O-1~~ **çözüldü (sahip):** runner soyutlaması; ilk runner Mac.
- **O-2 (Faz 2 brief'inde netleşecek):** CLI agent headless kimlik
  doğrulaması — Claude Code headless/SDK resmi destekli; Codex CLI vendor
  şartları Faz 2 brief'inde doğrulanacak; şartlar engel çıkarırsa
  API-anahtarlı fallback tasarlanır.
- **O-3 (açık):** `agents` (MVP 7) ile `harness` uzun vadede birleşir mi?
  Faz 3 sonrası değerlendirme; şimdilik ayrı.
- ~~O-4~~ **çözüldü (ADR-008 k.4, Codex 2. turla daraltıldı):** work_log
  istisnası DAR — otomatik onay yalnız sistem-üretimi etkinlik özetleri;
  konuşma-türevi adaylar §O private-flag kararı çözülene dek
  `pending_approval`; sensitivity + private-asla-cloud sözleşmesi aynen;
  başka otomatik-onaylı tür türetilemez (yenisi yeni ADR ister).
- **O-5 (açık, dış bağımlılık):** external MCP sunucusunun zamanlaması — Faz 5'in
  external ayağı kurulum tarafına bağlı.
- ~~O-6~~ **çözüldü (ADR-008 k.6):** cephe endpoint'i sadece chat;
  tool-calling geçmez, tool'lar yüksek-güven kanalda kalır.
