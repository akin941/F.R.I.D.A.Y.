import type {
  AgentFailureReason,
  AgentStatus,
  AgentStepKind,
  AgentStepStatus,
  ApprovalOrigin,
  ApprovalRequestedChannel,
  ApprovalStatus,
  CiConclusion,
  CodingSessionStatus,
  ConnectorWriteStatus,
  CoreBlockLabel,
  CoreBlockWriterKind,
  IngestionSourceKind,
  MappingRuleType,
  SelfAnchorGroup,
  DeployRunStatus,
  HarnessAgent,
  HarnessJobStatus,
  ImplFailureReason,
  ImplStatus,
  KnowledgeSensitivity,
  KnowledgeSourceType,
  KnowledgeStatus,
  MessageChannel,
  MemorySensitivity,
  PatKind,
  MemoryStatus,
  MemoryType,
  TaskPriority,
  TaskStatus,
} from "@/types/api";

/**
 * The typed Turkish message catalog (Phase 4 branch 3). Single locale, so
 * no i18n framework: one object, checked by `satisfies` so every domain
 * enum value has a label and every backend error code has a message
 * (`catalog.test.ts` reads backend/core/error_codes.py).
 *
 * The per-feature `labels.ts` modules re-export their maps from here and
 * keep only domain logic (status lists, poll cadences).
 */

type GateStatus = "pending" | "approved" | "rejected" | "expired";

/**
 * One message per stable backend error `code` (backend/core/error_codes.py).
 * `specific: false` marks codes too generic to beat a caller's own
 * context ("Görev oluşturulamadı." says more than "Geçersiz istek.").
 */
export const ERROR_MESSAGES = {
  // Generic (DRF / status fallbacks)
  invalid: { message: "Gönderilen bilgiler geçersiz.", specific: false },
  parse_error: { message: "İstek okunamadı.", specific: false },
  authentication_failed: {
    message: "Oturum doğrulanamadı. Lütfen yeniden giriş yap.",
    specific: true,
  },
  not_authenticated: { message: "Oturumun sona erdi. Lütfen yeniden giriş yap.", specific: true },
  token_not_valid: { message: "Oturumun sona erdi. Lütfen yeniden giriş yap.", specific: true },
  permission_denied: { message: "Bu işlem için yetkin yok.", specific: true },
  channel_forbidden: {
    message: "Bu işlem yalnızca güvenilen bir cihazın web oturumundan yapılabilir.",
    specific: true,
  },
  owner_required: { message: "Bu işlemi yalnızca kurulum sahibi yapabilir.", specific: true },
  token_kind_forbidden: {
    message: "Bu erişim token'ının türü bu işlem için kullanılamaz.",
    specific: true,
  },
  not_found: { message: "Aranan kayıt bulunamadı.", specific: false },
  method_not_allowed: { message: "Bu işlem desteklenmiyor.", specific: false },
  not_acceptable: { message: "Bu işlem desteklenmiyor.", specific: false },
  unsupported_media_type: { message: "Bu dosya ya da içerik türü desteklenmiyor.", specific: true },
  throttled: {
    message: "Çok fazla istek gönderildi. Biraz bekleyip tekrar dene.",
    specific: true,
  },
  throttle_backend_unavailable: {
    message: "Hız sınırlama hizmetine şu anda ulaşılamıyor. Biraz sonra tekrar dene.",
    specific: true,
  },
  conflict: { message: "Kayıt bu arada değişti. Sayfayı yenileyip tekrar dene.", specific: true },
  error: { message: "Sunucuda beklenmeyen bir hata oluştu.", specific: false },
  service_unavailable: {
    message: "Hizmete şu anda ulaşılamıyor. Biraz sonra tekrar dene.",
    specific: true,
  },
  // Approvals
  "approval.permission_denied": {
    message: "Rolün bu onay isteğine karar vermeye yetmiyor.",
    specific: true,
  },
  "approval.agent_forbidden": {
    message: "Ajan hesapları onay kararı veremez.",
    specific: true,
  },
  "approval.channel_forbidden": {
    message: "Onay kararları yalnızca web uygulamasından verilebilir.",
    specific: true,
  },
  "approval.invalid_transition": {
    message: "Bu onay isteği artık bu işleme uygun değil.",
    specific: true,
  },
  "approval.expired": { message: "Bu onay isteğinin süresi doldu.", specific: true },
  // Connectors
  "connector.permission_denied": {
    message: "Bağlantıları yalnızca kurulum sahibi web uygulamasından yönetebilir.",
    specific: true,
  },
  "connector.invalid_transition": {
    message: "Bu bağlantı bu çalışma alanında açılamaz (engelli ya da bu alan türüne uygun değil).",
    specific: true,
  },
  // Agents
  "agent.permission_denied": { message: "Rolün bu ajan işlemine yetmiyor.", specific: true },
  "agent.invalid_transition": {
    message: "Ajan ya da adım artık bu işleme uygun değil.",
    specific: true,
  },
  // Deploys
  "deploy.permission_denied": {
    message: "Dağıtım işlemlerini yalnızca kurulum sahibi yapabilir.",
    specific: true,
  },
  "deploy.invalid_transition": {
    message: "Bu dağıtım hedefi ya da çalıştırması artık bu işleme uygun değil.",
    specific: true,
  },
  "deploy.ci_required": {
    message:
      "Bu hedef, commit için başarılı bir CI sonucu istiyor; CI durumu başarılı değil ya da bilinmiyor.",
    specific: true,
  },
  // Harness
  "harness.permission_denied": { message: "Rolün bu harness işlemine yetmiyor.", specific: true },
  "harness.invalid_transition": {
    message: "Bu harness işi, deposu ya da ajanı artık bu işleme uygun değil.",
    specific: true,
  },
  "harness.pull_request_not_ready": {
    message: "Bu iş için PR açılamaz: yalnızca başarıyla tamamlanıp dalı gönderilmiş işler PR alabilir.",
    specific: true,
  },
  "harness.pull_request_exists": {
    message: "Bu iş için zaten bekleyen ya da açılmış bir PR isteği var.",
    specific: true,
  },
  "harness.github_unavailable": {
    message:
      "GitHub bu çalışma alanında kullanılamıyor: bağlantı kapalı, araç doğrulanmamış ya da depo GitHub'da kayıtlı değil.",
    specific: true,
  },
  // Knowledge
  "knowledge.permission_denied": { message: "Rolün bu belge işlemine yetmiyor.", specific: true },
  "knowledge.duplicate": {
    message: "Aynı içerik bu çalışma alanında zaten var.",
    specific: true,
  },
  "knowledge.invalid_upload": {
    message: "Dosya kabul edilmedi (boyut, tür ya da içerik uygun değil).",
    specific: true,
  },
  // Memory
  "memory.permission_denied": { message: "Rolün bu hafıza işlemine yetmiyor.", specific: true },
  "memory.invalid_transition": {
    message: "Bu hafıza kaydı artık bu işleme uygun değil.",
    specific: true,
  },
  "memory.duplicate": {
    message: "Aynı hafıza kaydı bu çalışma alanında zaten var.",
    specific: true,
  },
  "memory.core_block.permission_denied": {
    message:
      "Çekirdek blokları yalnızca sahip, web oturumundan düzenleyebilir; 'self' bloğu elle düzenlenemez.",
    specific: true,
  },
  "memory.core_block.invalid": {
    message: "Bu çekirdek blok değişikliği geçersiz (etiket, karakter sınırı ya da sürüm uygun değil).",
    specific: true,
  },
  "memory.core_block.stale_version": {
    message: "Blok sen açtıktan sonra değişti; sayfayı yenileyip tekrar dene.",
    specific: true,
  },
  "memory.core_block.weekly_cap": {
    message: "Bu hafta için çekirdek blok önerisi sınırına ulaşıldı.",
    specific: true,
  },
  // Tasks
  "task.permission_denied": { message: "Rolün bu görev işlemine yetmiyor.", specific: true },
  "task.invalid_transition": {
    message: "Bu görev artık bu işleme uygun değil.",
    specific: true,
  },
  "task.draft_already_confirmed": {
    message: "Bu taslaktan zaten bir görev oluşturuldu.",
    specific: true,
  },
  // Voice (Phase 6 branch 2)
  "voice.unsupported_audio": {
    message: "Ses kaydı okunamadı: 16 kHz mono 16-bit PCM WAV bekleniyordu.",
    specific: true,
  },
  "voice.upload_too_large": {
    message: "Konuşma çok uzun. En fazla 30 saniyelik bir cümle söyle.",
    specific: true,
  },
  "voice.no_local_provider": {
    message:
      "Kişisel veriyi işlemeye izinli bir konuşma sunucusu yok. Mac'teki yerel konuşma sunucusunu başlat.",
    specific: true,
  },
  "voice.stt_failed": { message: "Konuşma yazıya dökülemedi. Tekrar dene.", specific: true },
  "voice.tts_failed": { message: "Yanıt seslendirilemedi; metin ekranda.", specific: true },
  "voice.budget_exceeded": {
    message:
      "Ses bütçesi doldu. Bütçe yenilenene kadar sesli mod kullanılamaz; yazarak devam edebilirsin.",
    specific: true,
  },
  // Workspaces
  "workspace.permission_denied": {
    message: "Rolün bu çalışma alanını düzenlemeye yetmiyor.",
    specific: true,
  },
} as const satisfies Record<string, { message: string; specific: boolean }>;

export type ErrorCode = keyof typeof ERROR_MESSAGES;

export const tr = {
  common: {
    retry: "Tekrar dene",
    cancel: "Vazgeç",
    close: "Kapat",
    loading: "Yükleniyor…",
    noWorkspace: "Kullanılabilir çalışma alanı yok.",
    technicalDetails: "Teknik ayrıntılar",
    copy: "Kopyala",
    copied: "Kopyalandı",
    copyCode: "Kodu kopyala",
  },
  errors: {
    generic: "Bir şeyler ters gitti. Lütfen tekrar deneyin.",
    network: "Sunucuya ulaşılamadı. Bağlantını kontrol edip tekrar dene.",
    status: "HTTP durumu",
    code: "Hata kodu",
    detail: "Sunucu mesajı",
  },
  theme: {
    label: "Tema",
    light: "Açık",
    dark: "Koyu",
    system: "Sistem",
  },
  nav: {
    label: "Ana menü",
    openMenu: "Menüyü aç",
    menu: "Menü",
    logout: "Çıkış yap",
    groups: {
      work: "Çalışma",
      memory: "Hafıza",
      firm: "Firma",
      automation: "Otomasyon",
      settings: "Ayarlar",
    },
    items: {
      chat: "Sohbet",
      approvals: "Onaylar",
      harness: "Harness",
      memory: "Hafıza",
      knowledge: "Bilgi",
      tasks: "Görevler",
      today: "Bugün",
      trash: "Çöp kutusu",
      workspaces: "Firma dosyası",
      agents: "Ajanlar",
      devices: "Cihazlar",
      integrations: "Entegrasyonlar",
      connectors: "Bağlantılar",
      deploys: "Deploy hedefleri",
      voice: "Sesli",
      hud: "HUD",
      sessions: "Oturumlar",
      learning: "Sabah kartı",
      ingestion: "Öğrenme kaynakları",
      coreBlocks: "Çekirdek bloklar",
    },
    pendingApprovals: (count: number) => `${count} bekleyen onay`,
    pendingElsewhere: (count: number) => `Diğer çalışma alanlarında ${count} bekleyen onay`,
    liveWork: (count: number) => `${count} çalışan iş`,
  },
  workspace: {
    switchedByLink: (name: string) => `Bağlantı “${name}” çalışma alanını açtı.`,
    unknownFromLink: "Bağlantıdaki çalışma alanına erişimin yok; mevcut çalışma alanında kaldın.",
  },
  time: {
    justNow: "az önce",
  },
  // Phase 6 branch 3: push-to-talk voice (VoiceView, orb, shell mute).
  hud: {
    title: "Friday HUD",
    exit: "HUD'dan çık",
    // Every state as text (also announced through aria-live, A8).
    states: {
      idle: "Boşta",
      armed: "Uyandırma kelimesini bekliyor",
      listening: "Dinliyor",
      thinking: "Düşünüyor",
      "tool-running": "Araç çalışıyor",
      "awaiting-approval": "Onay bekliyor",
      speaking: "Konuşuyor",
      error: "Bir sorun var",
      muted: "Ses kapalı",
      disconnected: "Bağlantı koptu",
      "offline-local": "Çevrimdışı — yalnızca yerel",
    },
    stateAnnouncement: (label: string) => `Friday durumu: ${label}`,
    captions: {
      heading: "Altyazılar",
      heard: "Duyduğum",
      reply: "Yanıt",
      empty: "Konuştuğunda altyazılar burada görünür.",
      transcribing: "yazıya dökülüyor…",
      finishing: "devam edebilirsin…",
    },
    tasks: {
      heading: "Bugünün görevleri",
      empty: "Bugün için açık görev yok.",
      overdue: "gecikmiş",
      more: (count: number) => `+${count} görev daha`,
      all: "Tüm görevler",
    },
    approvals: {
      heading: "Onaylar",
      empty: "Bekleyen onay yok.",
      // D8: the HUD only links to the decision route; it never decides.
      open: "Onay sayfasında aç",
      more: (count: number) => `+${count} onay daha`,
      running: (count: number) => `${count} araç çalışıyor`,
    },
    cost: {
      heading: "Bugünkü maliyet",
      calls: (count: number) => `${count} model çağrısı`,
      failed: (count: number) => `${count} başarısız`,
      tokens: (input: number, output: number) => `${input} girdi / ${output} çıktı token`,
      localOnly: "Yalnızca yerel modeller (ücretsiz)",
      amount: (usd: string) => `$${usd}`,
    },
    health: {
      heading: "Sağlık",
      mic: "Mikrofon",
      micStates: {
        muted: "Susturuldu",
        open: "Açık",
        wake: "Uyandırma için açık",
        closed: "Kapalı",
      },
      stream: "HUD bağlantısı",
      streamStates: {
        connecting: "Bağlanıyor…",
        open: "Bağlı",
        lost: "Koptu — yeniden deneniyor",
      },
      providers: "Sağlayıcılar",
      providerStates: {
        ok: "çalışıyor",
        down: "yanıt vermiyor",
        unknown: "son çağrı yok",
        disabled: "devre dışı",
      },
      local: "yerel",
      cloud: "bulut",
      budgets: "Bütçeler",
      noBudgets: "Tanımlı bütçe yok.",
      budgetWindow: { daily: "günlük", monthly: "aylık" } as Record<string, string>,
      budgetExhausted: (name: string) => `${name} bütçesi doldu`,
      budgetUsage: (spent: string, limit: string) => `$${spent} / $${limit}`,
      preflight: "Son ön kontrol",
      preflightNone: "Henüz ön kontrol çalışmadı (manage.py preflight).",
      preflightStates: { pass: "geçti", warn: "uyarılarla geçti", fail: "başarısız" } as Record<
        string,
        string
      >,
      preflightCounts: (pass: number, fail: number, warn: number) =>
        `${pass} geçti · ${fail} başarısız · ${warn} uyarı`,
    },
    settings: {
      heading: "Bu cihazda",
      animateOnBattery: "Pilde boşta animasyon",
      earcons: "Durum sesleri",
    },
    noWorkspace: "Önce bir çalışma alanı seç.",
    miniOrb: (label: string) => `Friday: ${label} — HUD'u aç`,
    // Phase 8A branch 6: read-only HUD cards; decisions happen on their pages (D8).
    sessions: {
      heading: "Kodlama oturumları",
      empty: "Kayıtlı Claude Code oturumu yok.",
      failed: "Oturumlar yüklenemedi.",
      all: "Tüm oturumlar",
      more: (count: number) => `+${count} oturum daha`,
    },
    learning: {
      heading: "Sabah kartı",
      empty: "Dün geceden bekleyen öneri yok.",
      failed: "Öneriler yüklenemedi.",
      pending: (count: number) => `${count} öneri incelemeni bekliyor`,
      open: "Sabah kartını aç",
    },
  },
  voice: {
    orb: {
      idle: "Friday boşta",
      armed: "Friday dinlemeye hazır",
      listening: "Friday dinliyor",
      transcribing: "Friday yazıya döküyor",
      thinking: "Friday düşünüyor",
      speaking: "Friday konuşuyor",
      error: "Friday: bir sorun var",
    },
    phase: {
      idle: "Konuşmak için mikrofon düğmesine bas ya da Boşluk tuşunu basılı tut.",
      listening: "Dinliyorum… Bitirince bırak.",
      transcribing: "Yazıya döküyorum…",
      finishing: "Devam etmek istersen tekrar bas; birazdan gönderiyorum.",
      thinking: "Düşünüyorum…",
      speaking: "Konuşuyorum. Durdurmak için Escape.",
      error: "Bir sorun oluştu.",
    },
    micStart: "Konuşmaya başla",
    micStop: "Konuşmayı bitir",
    micHint: "Basılı tut ya da tıkla (Boşluk)",
    stop: "Durdur",
    previewToggle: "Göndermeden önce göster",
    reviewLabel: "Duyduğum metin — düzeltip gönderebilirsin",
    reviewSend: "Gönder",
    reviewDiscard: "Vazgeç",
    heard: "Sen",
    reply: "Friday",
    badge: "Sesli",
    badgeHint: "Bu mesaj sesle söylendi",
    mute: "Sesi kapat",
    unmute: "Sesi aç",
    mutedNotice: "Ses kapalı: Friday dinlemiyor ve konuşmuyor.",
    draftsHeading: "Taslaklar — onay ekrandan, tıklayarak",
    memoryProposed: "hafıza önerisi; Hafıza sayfasında onayını bekliyor",
    notices: {
      muted: "Ses kapalı; mikrofon açılmadı.",
      mute_unreadable: "Ses durumu okunamadı; güvenlik için mikrofon kapatıldı.",
      "voice.tts_failed": "Yanıtın bir kısmı seslendirilemedi; metin ekranda.",
      receipt_failed: "Maliyet bilgisi alınamadı.",
      memory_proposal_failed: "Hafıza önerisi kaydedilemedi.",
      self_trigger: "Friday kendi sesini duydu; bu söz yok sayıldı.",
    } as Record<string, string>,
    wake: {
      heading: "Uyandırma sözcüğü",
      description: (phrase: string) =>
        `"${phrase}" dediğinde Friday dinlemeye başlar. Uyandırma sözcüğü bu sekmede, cihazında algılanır; sözcükten önceki ses hiçbir yere gönderilmez.`,
      arm: (phrase: string) => `"${phrase}" modunu aç`,
      disarm: "Uyandırma modunu kapat",
      defaultHint: "Varsayılan bas-konuş; uyandırma modu yalnızca bu sekmede ve sen açınca çalışır.",
      indicator: (phrase: string) => `Dinliyor — "${phrase}"`,
      indicatorPaused: "Dinleme duraklatıldı (Friday konuşuyor)",
      indicatorCapturing: "Seni dinliyorum…",
      loading: "Uyandırma modeli yükleniyor…",
      threshold: "Algılama eşiği",
      thresholdHint: "Yüksek eşik daha az yanlış uyanma, daha çok kaçırma demek.",
      idleMinutes: "Şu kadar dakika algılama olmazsa kapat",
      state: {
        off: "Uyandırma modu kapalı.",
        loading: "Uyandırma modeli yükleniyor…",
        armed: "Uyandırma modu açık: mikrofon açık, yalnızca uyandırma sözcüğü bekleniyor.",
        capturing: "Uyandırma sözcüğü duyuldu; seni dinliyorum.",
        paused: "Friday konuşurken uyandırma duraklatıldı.",
        error: "Uyandırma modu açılamadı.",
      } as Record<string, string>,
      notices: {
        idle_disarmed: "Uzun süre algılama olmadığı için uyandırma modu kapatıldı.",
        auth_lost: "Oturum kapandığı için uyandırma modu kapatıldı.",
        storm_muted:
          "Bir dakika içinde çok fazla uyanma oldu; güvenlik için ses kapatıldı. Kontrol edip sesi yeniden aç.",
        muted: "Ses kapalı; uyandırma modu açılmadı.",
        mute_unreadable: "Ses durumu okunamadı; güvenlik için mikrofon kapatıldı.",
        mic_ended: "Mikrofon bağlantısı koptu; uyandırma modu kapatıldı.",
        gesture_required: "Uyandırma modu yalnızca düğmeye tıklayarak açılabilir.",
      } as Record<string, string>,
      errors: {
        model_unpinned:
          "Uyandırma modeli henüz sabitlenmedi (hey_friday eğitilip özeti model listesine yazılmalı). Bas-konuş çalışmaya devam ediyor.",
        model_missing: "Uyandırma modeli bulunamadı. Sunucuda `make voice-models` çalıştır.",
        model_hash_mismatch:
          "Uyandırma modeli doğrulanamadı (özet uyuşmuyor); güvenlik için açılmadı. `make voice-models` ile yeniden indir.",
        model_cross_origin: "Uyandırma modeli başka bir adresten yüklenemez.",
        model_unsupported: "Bu tarayıcı model doğrulamasını desteklemiyor.",
        wake_failed: "Uyandırma motoru başlatılamadı.",
      } as Record<string, string>,
    },
    errors: {
      mic_denied:
        "Mikrofon izni verilmedi. Tarayıcının adres çubuğundaki site izinlerinden mikrofona izin verip tekrar dene.",
      mic_missing: "Mikrofon bulunamadı. Bir mikrofon bağlayıp tekrar dene.",
      mic_insecure:
        "Mikrofon yalnızca güvenli bağlantıda (HTTPS ya da localhost) çalışır. Friday'yi güvenli adresten aç.",
      mic_failed: "Mikrofon başlatılamadı. Tekrar dene.",
      muted: "Ses kapalı; mikrofon açılmadı.",
      mute_unreadable: "Ses durumu okunamadı; güvenlik için mikrofon kapatıldı.",
      disconnected: "Bağlantı kesildi; güvenlik için mikrofon kapatıldı. Tekrar dene.",
      conversation_failed: "Konuşma başlatılamadı. Lütfen tekrar deneyin.",
      send_failed: "Mesaj gönderilemedi.",
      stream_error: "Yanıt alınamadı.",
    } as Record<string, string>,
  },
  chat: {
    conversations: "Sohbetler",
    openConversations: "Sohbet listesini aç",
    newChat: "Yeni sohbet",
    untitled: "Adsız sohbet",
    noConversations: "Henüz sohbet yok.",
    loadConversationsFailed: "Sohbetler yüklenemedi.",
    loadMessagesFailed: "Mesajlar yüklenemedi.",
    newChatFailed: "Yeni sohbet başlatılamadı. Lütfen tekrar deneyin.",
    pickConversation: "Bir sohbet seç ya da yeni bir sohbet başlat.",
    emptyConversation: "Friday'ye bir şey sor.",
    unavailable: "Bu sohbete artık erişilemiyor.",
    placeholder: "Friday'ye yaz…",
    composerLabel: "Mesaj",
    send: "Gönder",
    stop: "Durdur",
    assistantTyping: "Asistan yanıtlıyor",
    newMessages: "Yeni mesaj ↓",
    copyMessage: "Mesajı kopyala",
    interrupted: "Yanıt yarıda kaldı",
    failed: "Yanıt alınamadı",
    retry: "Tekrar dene",
    disconnected: "Yanıt tamamlanmadan bağlantı koptu.",
    refreshHistory: "Geçmişi yenile",
    throttled: (retryAfter: string | null) =>
      retryAfter
        ? `Çok hızlı mesaj gönderiyorsun. ${retryAfter}s sonra tekrar dene.`
        : "Çok hızlı mesaj gönderiyorsun. Lütfen biraz bekle.",
    sendFailed: "Mesaj gönderilemedi.",
    // SSE `error` event `error_type`s (assistant.services); anything else
    // falls back to `streamFailed`.
    streamErrors: {
      no_provider:
        "Şu anda yapılandırılmış bir yapay zeka sağlayıcısı yok. Lütfen daha sonra tekrar deneyin.",
      client_disconnect: "Bağlantı kesildi.",
      connector_local_model_required:
        "Bu kaynak kişisel veri içeriyor; okumak için kişisel veriye izinli yerel bir model gerekiyor. Hiçbir şey okunmadı.",
      connector_invalid_arguments:
        "İstek bu kaynak için anlaşılamadı ya da bu çalışma alanının dışında kalıyor. Hiçbir şey okunmadı.",
      connector_read_failed: "Kaynak şu anda okunamadı. Biraz sonra tekrar dene.",
      connector_reconnect_required:
        "Google bağlantısının yetkisi düşmüş. Sunucuda `manage.py connect_google` ile yeniden bağlan. Hiçbir şey okunmadı.",
    } as Record<string, string>,
    streamFailed: "Asistan beklenmedik bir hatayla karşılaştı. Lütfen tekrar deneyin.",
    private: "Özel",
    privateHint: "Özel sohbet: bu sohbetten hafıza çıkarılmaz.",
    channel: {
      web: "Web",
      cli: "CLI",
      telegram: "Telegram",
      openai: "OpenAI uyumlu istemci",
    } satisfies Record<MessageChannel, string> as Record<string, string>,
    viaChannel: (label: string) => `${label} üzerinden`,
    // Connector reads (Phase 5 branch 9): where a reply's context came from.
    source: (label: string) => `Kaynak: ${label}`,
    sourceHint:
      "Bu yanıt için dış bir sistemden bir okuma yapıldı; sonuç modele veri olarak verildi, talimat olarak değil.",
    sourceLabels: {
      github: "GitHub",
      google_mail: "Posta",
      google_calendar: "Takvim",
      home_assistant: "Ev",
    } as Record<string, string>,
    personalData: "Kişisel veri",
    personalDataNotice:
      "Bu sohbete kişisel veri (posta, takvim, ev ya da hassas bir depo) girdi. Sohbet özel yapıldı ve bundan sonra yalnızca kişisel veriye izinli (yerel) modellerle çalışır; bu geri alınamaz.",
    rename: "Yeniden adlandır",
    renameTitle: "Sohbeti yeniden adlandır",
    renameLabel: "Sohbet adı",
    renameHint: "Boş bırakırsan sohbet “Adsız sohbet” olarak görünür.",
    save: "Kaydet",
    saving: "Kaydediliyor…",
    renameFailed: "Sohbet adı değiştirilemedi.",
    markPrivate: "Özel yap",
    markPrivateTitle: "Bu sohbet özel yapılsın mı?",
    markPrivateDescription:
      "Özel sohbetlerden hafıza çıkarılmaz. Bu sohbetten daha önce çıkarılmış öneriler reddedilir, onaylanmış hafıza kayıtları arşivlenir. Bu işlem geri alınamaz.",
    markingPrivate: "Özel yapılıyor…",
    markPrivateFailed: "Sohbet özel yapılamadı.",
    sources: {
      title: "Kaynaklar",
      summary: (memories: number, chunks: number) =>
        [
          memories > 0 ? `${memories} hafıza kaydı` : null,
          chunks > 0 ? `${chunks} bilgi parçası` : null,
        ]
          .filter(Boolean)
          .join(", "),
      memory: (n: number) => `Hafıza kaydı ${n}`,
      chunk: (n: number) => `Bilgi parçası ${n}`,
      fromPersonal: "kişisel alandan",
      noLink: "belge artık yok",
    },
    taskDraft: {
      title: "Bu bir görev gibi görünüyor",
      due: (date: string) => `Son tarih ${date}`,
      confirm: "Görevi onayla",
      confirming: "Oluşturuluyor…",
      discard: "Vazgeç",
      failed: "Görev oluşturulamadı.",
      created: "Görev oluşturuldu",
      openTask: "Görevi aç",
    },
    toolDraft: {
      title: "Bu bir araç isteği gibi görünüyor",
      needsApproval: "Çalışmadan önce Onaylar sayfasından vereceğin açık onay gerekiyor.",
      confirm: "İsteği onayla",
      confirming: "İsteniyor…",
      discard: "Vazgeç",
      failed: "İstek oluşturulamadı.",
      readOnlyTitle: (tool: string) => `Araç isteği: ${tool}`,
      readOnlyHint:
        "Bu istek sohbette onaylandıysa durumunu ve kararını Onaylar sayfasında görebilirsin.",
      openApprovals: "Onaylara git",
    },
  },
  // The ADR-009 trash (Phase 4 branch 5).
  trash: {
    title: "Çöp kutusu",
    description: (days: number) =>
      `Silinen görevler ve belgeler ${days} gün burada kalır, sonra kalıcı olarak silinir. Geri yükleyebilir ya da hemen kalıcı olarak silinmeleri için ayrı bir onay isteyebilirsin.`,
    tasks: "Görevler",
    documents: "Belgeler",
    empty: "Çöp kutusu boş.",
    emptyHint: "Sildiğin görevler ve belgeler geri yüklenebilmeleri için burada bekler.",
    noTasks: "Çöp kutusunda görev yok.",
    noDocuments: "Çöp kutusunda belge yok.",
    loadFailed: "Çöp kutusu yüklenemedi.",
    deleted: (when: string) => `Silindi ${when}`,
    purgesAt: (date: string) => `Kalıcı silinme: ${date}`,
    restore: "Geri yükle",
    restoring: "Geri yükleniyor…",
    restoreFailed: "Geri yüklenemedi.",
    select: (title: string) => `Seç: ${title}`,
    selectAll: "Tümünü seç",
    clearSelection: "Seçimi temizle",
    selected: (count: number) => `${count} öğe seçildi`,
    purge: "Seçilenleri kalıcı olarak sil",
    purgeLimit: (max: number) => `Tek istekte en fazla ${max} öğe kalıcı olarak silinebilir.`,
    purgeTitle: "Kalıcı silme için onay istensin mi?",
    purgeDescription: (count: number) =>
      `Seçilen ${count} öğe için bir onay isteği oluşturulur. Kalıcı silme geri alınamaz; yalnızca bu isteği Onaylar sayfasında ayrıca onayladığında gerçekleşir. O zamana kadar öğeler çöp kutusunda kalır ve geri yüklenebilir.`,
    purgeConfirm: "Onay isteği oluştur",
    purging: "İsteniyor…",
    purgeFailed: "Kalıcı silme isteği oluşturulamadı.",
    purgeRequested: "Kalıcı silme onay isteği oluşturuldu. Karar Onaylar sayfasında verilir.",
    openRequest: "Onay isteğini aç",
  },
  // Confirmation dialogs in front of every destructive action (Phase 4
  // branch 5). Each names the object and states the consequence.
  confirm: {
    memory: {
      rejectTitle: "Bu hafıza önerisi reddedilsin mi?",
      reject: (title: string) =>
        `“${title}” hafızaya eklenmez. Reddedilen öneri listede kalır; istersen sonra kalıcı olarak silebilirsin.`,
      rejectConfirm: "Evet, reddet",
      rejecting: "Reddediliyor…",
      forgetTitle: "Bu hafıza kaydı unutulsun mu?",
      forget: (title: string) =>
        `“${title}” arşivlenir ve Friday onu artık kullanmaz. Arşivden istediğin zaman geri yükleyebilirsin.`,
      forgetConfirm: "Evet, unut",
      forgetting: "Unutuluyor…",
      deleteTitle: "Reddedilen öneri kalıcı olarak silinsin mi?",
      delete: (title: string) =>
        `“${title}” kalıcı olarak silinir. Hiç onaylanmamış bir öneri olduğu için çöp kutusuna gitmez; bu işlem geri alınamaz.`,
      deleteConfirm: "Evet, kalıcı olarak sil",
      failed: "İşlem tamamlanamadı.",
    },
    trash: {
      deleting: "Taşınıyor…",
      confirm: "Çöp kutusuna taşı",
      taskTitle: "Bu görev çöp kutusuna taşınsın mı?",
      documentTitle: "Bu belge çöp kutusuna taşınsın mı?",
      task: (title: string, days: number) =>
        `“${title}” çöp kutusuna taşınır. ${days} gün içinde Çöp kutusu sayfasından geri yükleyebilirsin; bu sürenin sonunda kalıcı olarak silinir.`,
      document: (title: string, days: number) =>
        `“${title}” çöp kutusuna taşınır ve aramalarda artık kullanılmaz. ${days} gün içinde Çöp kutusu sayfasından geri yükleyebilirsin; bu sürenin sonunda kalıcı olarak silinir.`,
      taskFailed: "Görev silinemedi.",
      documentFailed: "Belge silinemedi.",
    },
    token: {
      title: "Bu token iptal edilsin mi?",
      description: (name: string) =>
        `“${name}” ile yapılan istekler hemen reddedilir. İptal geri alınamaz; gerekirse yeni bir token oluşturursun.`,
      confirm: "Evet, iptal et",
      busy: "İptal ediliyor…",
      failed: "Token iptal edilemedi.",
    },
    telegram: {
      title: "Telegram bağlantısı kaldırılsın mı?",
      description:
        "Bu çalışma alanına bağlı Telegram sohbeti artık Friday'ye mesaj iletemez. Yeniden bağlamak için yeni bir bağlantı kodu gerekir.",
      confirm: "Evet, kaldır",
      busy: "Kaldırılıyor…",
      failed: "Sohbet bağlantısı kaldırılamadı.",
    },
    device: {
      revokeTitle: "Bu cihaz iptal edilsin mi?",
      revoke: (name: string) =>
        `“${name}” oturumunu hemen kaybeder. Yeniden kullanmak için yeniden eşleştirilmesi gerekir; bu işlem geri alınamaz.`,
      revokeConfirm: "Evet, iptal et",
      revoking: "İptal ediliyor…",
      revokeFailed: "Cihaz iptal edilemedi.",
      forgetTitle: "Bu cihaz unutulsun mu?",
      forget:
        "Bu tarayıcı artık güvenilen bir cihaz olmaz ve oturumun kapanır. Tekrar kullanmak için yeni bir eşleştirme kodu gerekir.",
      forgetConfirm: "Evet, unut",
      forgetting: "Unutuluyor…",
      forgetFailed: "Bu cihaz unutulamadı.",
    },
    remoteKey: {
      createTitle: "Uzaktan erişim anahtarı oluşturulsun mu?",
      create:
        "Anahtarı bilen biri herhangi bir ağdan yeni cihaz eşleştirebilir. Anahtar bir kez gösterilir; her kullanımı tüm cihazlarda bildirilir.",
      createConfirm: "Evet, oluştur",
      rotateTitle: "Uzaktan erişim anahtarı yenilensin mi?",
      rotate:
        "Mevcut anahtar hemen geçersiz olur ve yerine yenisi oluşturulur. Eski anahtarla artık cihaz eşleştirilemez.",
      rotateConfirm: "Evet, yenile",
      revokeTitle: "Uzaktan erişim anahtarı kapatılsın mı?",
      revoke:
        "Anahtar hemen geçersiz olur; yerel ağ dışından yalnızca güçlü kodla cihaz eşleştirilebilir.",
      revokeConfirm: "Evet, kapat",
      busy: "İşleniyor…",
      createFailed: "Uzaktan erişim anahtarı oluşturulamadı.",
      revokeFailed: "Uzaktan erişim anahtarı kapatılamadı.",
    },
    agent: {
      title: "Bu ajan iptal edilsin mi?",
      description: (goal: string) =>
        `“${goal}” durdurulur ve kalan adımları çalıştırılmaz. İptal edilen ajan devam ettirilemez.`,
      confirm: "Evet, iptal et",
      busy: "İptal ediliyor…",
      failed: "Ajan iptal edilemedi.",
    },
    harnessJob: {
      title: "Bu keşif işi iptal edilsin mi?",
      description: (goal: string) =>
        `“${goal}” için çalışan inceleme durdurulur ve brifing üretilmez. İptal edilen iş devam ettirilemez.`,
      confirm: "Evet, iptal et",
      busy: "İptal ediliyor…",
      failed: "İş iptal edilemedi.",
    },
    implementationJob: {
      title: "Bu uygulama işi iptal edilsin mi?",
      description:
        "Bu uygulama işi iptal edilsin mi? Bekleyen onay istekleri geri alınır ve iş kaldığı yerden devam ettirilemez.",
      confirm: "Evet, iptal et",
      busy: "İptal ediliyor…",
      failed: "İş iptal edilemedi.",
    },
    repo: {
      deleteTitle: "Bu depo kaydı silinsin mi?",
      delete: (name: string) =>
        `“${name}” harness kayıtlarından kaldırılır; uzak depoya dokunulmaz. İş geçmişi olan bir depo silinemez — onun yerine devre dışı bırak.`,
      deleteConfirm: "Evet, sil",
      deleting: "Siliniyor…",
      deleteFailed: "Depo silinemedi.",
    },
  },
  markdown: {
    imageOmitted: (alt: string) => (alt ? `[görsel gösterilmedi: ${alt}]` : "[görsel gösterilmedi]"),
  },
  status: {
    approval: {
      pending: "Beklemede",
      approved: "Onaylandı",
      rejected: "Reddedildi",
      expired: "Süresi doldu",
    } satisfies Record<ApprovalStatus, string>,
    // vault_export's (and any generic tool's) execution statuses. Harness
    // tools report the implementation job's own status instead.
    execution: {
      pending: "Çalışması bekleniyor",
      running: "Çalışıyor",
      succeeded: "Başarılı",
      failed: "Başarısız",
    } as Record<string, string>,
    deployRun: {
      pending: "Başlaması bekleniyor",
      checking: "Hedef kontrol ediliyor",
      deploying: "Dağıtılıyor",
      health_checking: "Sağlık kontrolü",
      rolling_back: "Geri alınıyor",
      succeeded: "Başarılı",
      succeeded_degraded: "Kısmen başarılı (sağlık adresi başarısız)",
      rolled_back: "Geri alındı",
      rollback_failed: "Geri alma başarısız",
      health_failed: "Sağlık kontrolü başarısız",
      unknown_outcome: "Sonuç bilinmiyor",
      refused: "Reddedildi (hiçbir şeye dokunulmadı)",
      failed: "Başarısız (dağıtım yapılmadı)",
      cancelled: "İptal edildi",
    } satisfies Record<DeployRunStatus, string>,
    connectorWrite: {
      pending: "Gönderilmesi bekleniyor",
      running: "Gönderiliyor",
      succeeded: "Gönderildi",
      failed: "Başarısız — hiçbir şey gönderilmedi",
      unknown_outcome: "Sonuç bilinmiyor — yeniden gönderilmez",
    } satisfies Record<ConnectorWriteStatus, string>,
    ci: {
      success: "CI başarılı",
      failure: "CI başarısız",
      pending: "CI sürüyor",
      unknown: "CI durumu bilinmiyor",
    } satisfies Record<CiConclusion, string>,
    pin: {
      ok: "Doğrulandı",
      mismatch: "Şema değişti — kapalı",
      missing: "Sunucuda yok — kapalı",
      unchecked: "Henüz kontrol edilmedi",
    } as Record<string, string>,
    agent: {
      planning: "Planlanıyor",
      running: "Çalışıyor",
      blocked_on_approval: "Onay bekliyor",
      blocked_on_confirmation: "Onay bekliyor",
      completed: "Tamamlandı",
      failed: "Başarısız",
      cancelled: "İptal edildi",
    } satisfies Record<AgentStatus, string>,
    agentStep: {
      pending: "Beklemede",
      running: "Çalışıyor",
      succeeded: "Başarılı",
      failed: "Başarısız",
      blocked_on_approval: "Onay bekliyor",
      blocked_on_confirmation: "Onay bekliyor",
      skipped: "Atlandı",
    } satisfies Record<AgentStepStatus, string>,
    harnessJob: {
      pending: "Beklemede",
      running: "Çalışıyor",
      succeeded: "Tamamlandı",
      failed: "Başarısız",
      cancelled: "İptal edildi",
    } satisfies Record<HarnessJobStatus, string>,
    implementation: {
      pending_gate_a: "Plan onayı bekleniyor (Kapı A)",
      implementing: "Uygulanıyor",
      pending_review: "İnceleniyor",
      pending_gate_b: "Gönderim onayı bekleniyor (Kapı B)",
      pushing: "Gönderiliyor",
      succeeded: "Tamamlandı",
      failed: "Başarısız",
      cancelled: "İptal edildi",
    } satisfies Record<ImplStatus, string>,
    implementationStep: {
      pending_gate_a: "Plan onayı",
      implementing: "Uygulama",
      pending_review: "İnceleme",
      pending_gate_b: "Gönderim onayı",
      pushing: "Gönderim",
      succeeded: "Tamamlandı",
      failed: "Başarısız",
      cancelled: "İptal edildi",
    } satisfies Record<ImplStatus, string>,
    gate: {
      pending: "Karar bekliyor",
      approved: "Onaylandı",
      rejected: "Reddedildi",
      expired: "Süresi doldu / geri alındı",
    } satisfies Record<GateStatus, string>,
    review: {
      pending: "Beklemede",
      running: "Çalışıyor",
      succeeded: "Tamamlandı",
      failed: "Başarısız",
    } satisfies Record<"pending" | "running" | "succeeded" | "failed", string>,
    reviewVerdict: {
      approved: "Onaylandı",
      changes_requested: "Değişiklik istendi",
    } as Record<string, string>,
    memory: {
      pending_approval: "Onay bekliyor",
      approved: "Onaylandı",
      rejected: "Reddedildi",
      archived: "Arşivlendi",
    } satisfies Record<MemoryStatus, string>,
    knowledge: {
      pending: "Beklemede",
      processing: "İşleniyor",
      ready: "Hazır",
      failed: "Başarısız",
    } satisfies Record<KnowledgeStatus, string>,
    task: {
      open: "Açık",
      done: "Tamamlandı",
      cancelled: "İptal edildi",
    } satisfies Record<TaskStatus, string>,
  },
  agent: {
    // `""` (no failure) is never rendered -- callers only show a set reason.
    failureReason: {
      plan_too_large: "Plan çok büyük",
      plan_empty: "Plan boş",
      planner_error: "Planlayıcı hatası",
      consecutive_failures: "Art arda çok fazla başarısız adım",
      gateway_budget_exceeded: "Model çağrı bütçesi aşıldı",
    } satisfies Record<Exclude<AgentFailureReason, "">, string>,
    stepKind: {
      read: "Oku",
      tool_request: "Araç isteği",
      task_draft: "Görev taslağı",
    } satisfies Record<AgentStepKind, string>,
  },
  harness: {
    agent: {
      claude_code: "Claude Code",
      codex_cli: "Codex CLI",
    } satisfies Record<HarnessAgent, string>,
    // The implementation job's failure vocabulary is a superset of the
    // discovery job's, so one map covers both.
    failureReason: {
      timeout: "Zaman aşımı",
      runner_error: "Çalıştırıcı hatası",
      budget_exceeded: "Bütçe aşıldı",
      killed: "Durduruldu",
      gate_a_rejected: "Plan onayı (Kapı A) reddedildi",
      gate_b_rejected: "Gönderim onayı (Kapı B) reddedildi",
      gate_expired: "Onayın süresi doldu",
      test_command_not_configured: "Depo için test komutu tanımlı değil",
      test_result_unavailable: "Test sonucu alınamadı",
      tests_failed: "Testler başarısız",
      review_changes_requested: "İnceleme değişiklik istedi",
      review_unavailable: "İnceleme sonucu alınamadı",
      worktree_expired: "Çalışma ağacının süresi doldu",
      push_failed: "Gönderim (push) başarısız",
      no_changes: "Ajan hiçbir değişiklik üretmedi",
      secret_leak: "Değişikliklerde gizli bilgi sızıntısı tespit edildi",
    } satisfies Record<Exclude<ImplFailureReason, "">, string>,
    // The routed console (Phase 4 branch 5).
    console: {
      backToConsole: "Harness'e dön",
      backToImplementations: "Uygulama işlerine dön",
      repos: "Depolar",
      live: "Canlı işler",
      noLive: "Şu anda çalışan ya da onay bekleyen iş yok.",
      discoveryJob: "Keşif işi",
      implementationJob: "Uygulama işi",
      detailTabs: "İş ayrıntıları",
      tabs: {
        briefing: "Brifing",
        plan: "Plan",
        review: "İnceleme",
        diff: "Diff",
        transcripts: "Transkriptler",
      },
      ownSummaryNote: "Özet ve bulgular Friday'nin kendi özetidir (model üretimi).",
      noSummaryButTranscript:
        "Özet üretilemedi. Ajanın ham çıktısı Transkriptler sekmesinde, güvenilmeyen içerik olarak duruyor.",
      briefingPending: "Brifing iş bittiğinde burada görünecek.",
      noTranscript: "Henüz ham çıktı yok.",
      noReviews: "Henüz inceleme yok.",
      rawOnTranscriptsTab:
        "İnceleyicinin ham çıktısı Transkriptler sekmesinde, güvenilmeyen içerik olarak duruyor.",
      noCommitYet: "Henüz commit üretilmedi.",
    },
    repos: {
      title: "Depolar",
      description:
        "Harness'in çalışabileceği kayıtlı depolar. Bir depo etkinleştirilene kadar hiçbir iş onu kullanamaz.",
      create: "Depo kaydet",
      createTitle: "Yeni depo kaydet",
      createSubmit: "Kaydet",
      createFailed: "Depo kaydedilemedi.",
      editTitle: (name: string) => `“${name}” deposunu düzenle`,
      editSubmit: "Değişiklikleri kaydet",
      editFailed: "Depo güncellenemedi.",
      edit: "Düzenle",
      saving: "Kaydediliyor…",
      loadFailed: "Depolar yüklenemedi.",
      empty: "Henüz kayıtlı depo yok.",
      emptyHint: "Bir depo kaydet ve etkinleştir; keşif ve uygulama işleri onu seçebilir.",
      formDescription:
        "Kimlik bilgisi saklanmaz: klonlama yetkisi sunucu ortamından gelir. Uygulama işleri için test komutu gerekir.",
      name: "Ad",
      gitUrl: "Git adresi",
      gitUrlHint:
        "Token, parola, “?” ya da “#” içermeyen düz bir adres (ör. git@github.com:sahip/depo.git).",
      defaultBranch: "Varsayılan dal",
      testCommand: "Test komutu",
      testCommandHint:
        "Uygulama işlerinde değişiklikten sonra çalıştırılır; boş bırakılırsa uygulama işleri reddedilir.",
      noTestCommand: "Test komutu tanımlı değil — uygulama işleri bu depoyu kullanamaz.",
      enabled: "Etkin",
      disabled: "Devre dışı",
      enabledLabel: "Etkin",
      enabledHint: "Devre dışı bir depo hiçbir harness işinde seçilemez.",
      sensitive: "Hassas",
      sensitiveLabel: "Hassas müşteri işi",
      sensitiveHint: "Bu depodaki işler hassas olarak işaretlenir.",
      selfRepo: "Friday'nin kendi deposu",
      selfRepoHint:
        "Bu depo Friday'nin kendi kurulumuna çözülüyor: kendini değiştirme koruması (ADR-004) uygulama işlerini reddeder. Salt okunur keşif çalışır.",
      selfRepoUnconfigured:
        "HARNESS_SELF_REPO_IDENTIFIERS tanımlı değil: kendini değiştirme koruması hangi deponun Friday olduğunu bilemediği için tüm uygulama işlerini reddeder. Keşif işleri çalışır.",
    },
  },
  approvals: {
    // Human title per tool; a tool without one keeps its raw `tool_name`.
    toolTitles: {
      harness_implement: "Uygulama planı onayı (Kapı A)",
      harness_push: "Gönderim onayı (Kapı B)",
      vault_export: "Obsidian vault dışa aktarımı",
      purge_trash: "Çöp kutusundan kalıcı silme",
      deploy: "Deploy onayı",
      github_open_pr: "GitHub PR açma onayı",
      ha_call_service: "Ev (Home Assistant) komutu onayı",
      mail_send: "E-posta gönderme onayı",
      calendar_create_event: "Takvim etkinliği oluşturma onayı",
    } as Record<string, string>,
    // Generic connector writes (`mcp_<connector>_<tool>`).
    connectorWriteTitle: (connector: string, tool: string) =>
      `${connector || "Bağlantı"} yazma onayı (${tool})`,
    // What deciding means, stated in the decision dialog (per-tool
    // renderers, components/approvals/renderers/).
    consequence: {
      harness_implement: {
        approve:
          "Onaylarsan ajan bu plana göre ayrı bir çalışma ağacında değişiklik yapmaya başlar. Dal, ayrı bir gönderim onayı (Kapı B) olmadan uzak depoya gönderilmez.",
        approveWith: (branch: string, repo: string) =>
          `Onaylarsan ajan “${repo}” deposunda, ayrı bir çalışma ağacında “${branch}” dalı üzerinde değişiklik yapmaya başlar. Dal, ayrı bir gönderim onayı (Kapı B) olmadan uzak depoya gönderilmez.`,
        reject:
          "Reddedersen uygulama işi başarısız olarak kapanır; depoda hiçbir değişiklik yapılmaz.",
      },
      harness_push: {
        approve: "Onaylarsan bu dal uzak depoya gönderilir (push). Gönderim geri alınamaz.",
        approveWith: (branch: string) =>
          `Onaylarsan “${branch}” dalı kayıtlı uzak depoya gönderilir (push). Gönderim geri alınamaz.`,
        reject: "Reddedersen değişiklikler gönderilmez ve uygulama işi başarısız olarak kapanır.",
      },
      vault_export: {
        approve:
          "Onaylarsan bu çalışma alanının onaylı hafıza kayıtları Obsidian vault'a (dosya sistemine) yazılır; özel kayıtlar yalnızca _private altına gider.",
        reject: "Reddedersen dışa aktarma çalışmaz; vault'a hiçbir şey yazılmaz.",
      },
      purge_trash: {
        approve: "Onaylarsan çöp kutusundaki bu öğeler kalıcı olarak silinir. Bu işlem geri alınamaz.",
        approveWith: (tasks: number, documents: number) =>
          `Onaylarsan çöp kutusundaki ${tasks} görev ve ${documents} belge kalıcı olarak silinir. Bu işlem geri alınamaz; bu arada geri yüklenen öğeler silinmez.`,
        reject:
          "Reddedersen öğeler çöp kutusunda kalır; saklama süresi dolunca yine otomatik olarak silinir.",
      },
      deploy: {
        approve:
          "Onaylarsan Friday bu commit'i hedefe dağıtır (deploy). Sağlık kontrolü ve gerekirse geri alma, yukarıdaki plana göre çalışır.",
        approveWith: (sha: string, target: string, rollback: boolean, rollbackSha: string) =>
          `Onaylarsan Friday “${sha}” commit'ini ${target} hedefine dağıtır. ` +
          (rollback
            ? `Sağlık kontrolü başarısız olursa yalnızca önceki commit'e (“${rollbackSha}”) otomatik geri alınır.`
            : "Sağlık kontrolü başarısız olursa hiçbir şey geri alınmaz; yeni bir karar gerekir."),
        reject: "Reddedersen hiçbir şey dağıtılmaz; hedef olduğu gibi kalır.",
      },
      github_open_pr: {
        approve:
          "Onaylarsan Friday GitHub'da yukarıdaki başlık ve açıklamayla bir pull request açar. PR açmak dış bir sistemde kalıcı bir kayıt oluşturur.",
        approveWith: (repo: string, head: string, base: string) =>
          `Onaylarsan Friday ${repo} deposunda “${head}” → “${base}” pull request'ini tam olarak yukarıdaki alanlarla açar. Friday PR'ı birleştirmez (merge).`,
        reject: "Reddedersen GitHub'da hiçbir şey açılmaz.",
      },
      ha_call_service: {
        approve:
          "Onaylarsan Friday Home Assistant'a tam olarak yukarıdaki hizmet çağrısını bir kez gönderir; evdeki cihaz gerçekten değişir.",
        approveWith: (service: string) =>
          `Onaylarsan Friday Home Assistant'a “${service}” çağrısını tam olarak yukarıdaki değerlerle bir kez gönderir; evdeki cihaz gerçekten değişir.`,
        reject: "Reddedersen Home Assistant'a hiçbir şey gönderilmez.",
      },
      mail_send: {
        approve:
          "Onaylarsan Friday bu e-postayı bağlı Google hesabından, yukarıdaki alıcılara tam olarak bu metinle gönderir. Gönderilen e-posta geri alınamaz.",
        reject: "Reddedersen e-posta gönderilmez.",
      },
      calendar_create_event: {
        approve:
          "Onaylarsan Friday bu etkinliği bağlı Google takvimine yukarıdaki alanlarla oluşturur; katılımcı varsa Google onlara davet e-postası gönderir.",
        reject: "Reddedersen takvime hiçbir şey eklenmez.",
      },
      connector_write: {
        approve:
          "Onaylarsan Friday bu isteği dış sisteme tam olarak yukarıdaki argümanlarla bir kez gönderir. Sonuç belirsiz kalırsa yeniden gönderilmez.",
        reject: "Reddedersen dış sisteme hiçbir şey gönderilmez.",
      },
      generic: {
        approve: "Onaylarsan bu istek çalıştırılır.",
        reject: "Reddedersen bu istek çalıştırılmaz.",
      },
    },
    noSummary: "Bu istek için özet bilgisi yok.",
    purgeSummary: (tasks: number, documents: number) =>
      `Kalıcı olarak silinecek: ${tasks} görev, ${documents} belge`,
    more: (count: number) => `+${count} daha`,
    openJob: "Uygulama işini aç",
    origin: {
      human: "İnsan",
      agent: "Ajan",
      harness: "Harness",
    } satisfies Record<ApprovalOrigin, string> as Record<string, string>,
    channel: {
      web: "Web",
      cli: "CLI",
      telegram: "Telegram",
      system: "Sistem",
      unknown: "Bilinmiyor",
    } satisfies Record<ApprovalRequestedChannel, string> as Record<string, string>,
    inbox: {
      title: "Onaylar",
      description:
        "Tüm firmalarındaki, insan kararı gerektiren yazma/silme/dağıtım/dışa-gönderme istekleri. Karar yalnızca bu web oturumundan verilir.",
      pending: "Bekleyenler",
      noPending: "Karar bekleyen bir şey yok.",
      history: "Karara bağlananlar",
      noHistory: "Bu filtrelere uyan karara bağlanmış istek yok.",
      loadFailed: "Onay istekleri yüklenemedi.",
      filters: "Filtreler",
      statusFilter: "Durum filtresi",
      toolFilter: "Araç filtresi",
      workspaceFilter: "Çalışma alanı filtresi",
      originFilter: "Kaynak filtresi",
      channelFilter: "Kanal filtresi",
      allStatuses: "Tüm durumlar",
      allTools: "Tüm araçlar",
      allWorkspaces: "Tüm çalışma alanları",
      allOrigins: "Tüm kaynaklar",
      allChannels: "Tüm kanallar",
      clearFilters: "Filtreleri temizle",
      unknownWorkspace: "Bilinmeyen çalışma alanı",
      shortcuts:
        "Klavye: j / k istekler arasında gezinir, Enter ayrıntıyı açar. Onay ya da ret için tek tuş kısayolu yoktur.",
      details: "Ayrıntılar",
    },
    meta: {
      requested: (when: string) => `İstendi ${when}`,
      decided: (when: string) => `Karara bağlandı ${when}`,
      via: (origin: string, channel: string) => `Kaynak: ${origin} · Kanal: ${channel}`,
      expiresIn: (remaining: string) => `Karar için kalan süre: ${remaining}`,
      expiredNow: "Süresi doldu — artık karar verilemez.",
    },
    detail: {
      back: "Onaylara dön",
      loadFailed: "Bu onay isteği yüklenemedi.",
    },
    decision: {
      approve: "Onayla",
      reject: "Reddet",
      approveTitle: "Bu istek onaylansın mı?",
      rejectTitle: "Bu istek reddedilsin mi?",
      approveConfirm: "Evet, onayla",
      rejectConfirm: "Evet, reddet",
      busy: "Gönderiliyor…",
      failed: "İşlem tamamlanamadı.",
      subject: (title: string, workspace: string) => `${title} — ${workspace}`,
    },
  },
  memory: {
    type: {
      semantic: "Anlamsal",
      episodic: "Anısal",
      preference: "Tercih",
      procedural: "Yordamsal",
      // System-generated activity summaries (ADR-008 k.4) -- never
      // user-creatable; only shown where an existing work_log is rendered.
      work_log: "Sistem günlüğü",
    } satisfies Record<MemoryType, string>,
    sensitivity: {
      normal: "Normal",
      private: "Özel",
    } satisfies Record<MemorySensitivity, string>,
  },
  // Phase 8A branch 6: the learning-memory screens.
  learning: {
    sourceKind: {
      git: "Git commit'leri",
      harness: "Harness işleri",
      claude_code_hooks: "Claude Code kancaları",
      calendar: "Takvim",
    } satisfies Record<IngestionSourceKind, string>,
    subnav: {
      label: "Öğrenen hafıza",
      learning: "Sabah kartı",
      ingestion: "Kaynaklar",
      coreBlocks: "Çekirdek bloklar",
    },
    card: {
      title: "Sabah kartı",
      description:
        "Friday dün gece senin kendi sözlerinden bunları öğrenmeyi öneriyor. Hiçbiri sen onaylamadan hafızaya girmez; her öneri, kaynağındaki senin cümleni gösterir.",
      loadFailed: "Öneriler yüklenemedi.",
      empty: "İncelenecek öneri yok. Yeni öneriler her gece en fazla 10 tane gelir.",
      since: (date: string) => `${date} tarihinden beri`,
      count: (count: number) => `${count} öneri`,
      quote: "Senin sözün",
      quoteMissing: "Kaynak alıntısı yok — bu öneriyi onaylamadan önce dikkatle incele.",
      source: (kind: string, date: string) => `${kind} · ${date}`,
      coreBlock: (label: string) => `Çekirdek blok önerisi: ${label}`,
      coreBlockNote: "Çekirdek blok önerileri tek tek onaylanır; onaylanınca bloğun yeni sürümü olur.",
      inferred: "Model çıkarımı",
      diff: "Önerilen değişiklik",
      approve: "Onayla",
      edit: "Düzenle",
      reject: "Reddet",
      save: "Kaydet ve onayla",
      saveOnly: "Kaydet",
      busy: "İşleniyor…",
      titleLabel: "Başlık",
      contentLabel: "İçerik",
      actionFailed: "İşlem tamamlanamadı.",
      approved: "Onaylandı.",
      rejected: "Reddedildi.",
      bulkHeading: "Sistem günlüğü önerileri",
      bulkHint: "Toplu onay yalnızca sistem günlüğü (work_log) önerileri için vardır.",
      bulkApprove: (count: number) => `${count} günlük kaydını birlikte onayla`,
      bulkTitle: (count: number) => `${count} sistem günlüğü kaydı onaylansın mı?`,
      bulkDescription:
        "Yalnızca sistem günlüğü kayıtları birlikte onaylanır. Diğer öneriler tek tek karar ister.",
      bulkFailed: "Toplu onay yapılamadı.",
      rejectTitle: "Öneri reddedilsin mi?",
      rejectDescription: (title: string) => `“${title}” hafızaya eklenmeyecek.`,
    },
    ingestion: {
      title: "Öğrenme kaynakları",
      description:
        "Friday'nin öğrendiği kaynaklar, nereye eşlendikleri ve ne tuttukları. Saklanan her metin önce temizlenir (sırlar ve kişisel veriler yer tutucuyla değiştirilir).",
      noPersonal: "Kaynaklar kişisel çalışma alanında tutulur; sahibi olduğun bir kişisel çalışma alanı bulunamadı.",
      loadFailed: "Kaynaklar yüklenemedi.",
      empty: "Kaynak yok. Sunucuda `manage.py seed_ingestion_sources` çalıştırılmalı.",
      enabled: "Açık",
      disabled: "Kapalı",
      paused: "Duraklatıldı",
      forgotten: "Unutuldu",
      enable: "Aç",
      disable: "Kapat",
      pause: "Duraklat",
      resume: "Sürdür",
      details: "Ayrıntılar",
      hideDetails: "Ayrıntıları gizle",
      today: (stored: number, dropped: number) => `Bugün: ${stored} saklandı · ${dropped} düştü`,
      budget: (spent: number, budget: number) => `Token: ${spent} / ${budget} (günlük)`,
      budgetLabel: "Günlük token bütçesi",
      saveBudget: "Bütçeyi kaydet",
      toggleFailed: "Kaynak güncellenemedi.",
      detailFailed: "Kaynak ayrıntıları yüklenemedi.",
      counters: "Son 30 gün",
      counterHeaders: {
        day: "Gün",
        stored: "Saklanan",
        duplicate: "Tekrar",
        dropped: "Düşen (eşlenmemiş / duraklatılmış)",
        tokens: "Token",
        proposals: "Öneri (oluşan / kabul / ret)",
      },
      noCounters: "Henüz sayaç yok.",
      acceptance: (rate: string) => `Kabul oranı: ${rate}`,
      acceptanceNone: "Kabul oranı: henüz karar yok",
      scrubberHits: "Temizleyici eşleşmeleri",
      noScrubberHits: "Temizleyici eşleşmesi yok.",
      lastItems: "Son kayıtlar",
      noItems: "Kayıt yok.",
      ownerText: "Sahip metni",
      contextText: "Bağlam (alıntılanamaz)",
      extracted: "İşlendi",
      notExtracted: "İşlenmedi",
      expires: (date: string) => `Silinme: ${date}`,
      unmapped: "Eşlenmemiş anahtarlar",
      unmappedHint: "Bu anahtarlar hiçbir kurala uymadığı için saklanmadı. Bir kural ekleyerek eşleyebilirsin.",
      noUnmapped: "Eşlenmemiş anahtar yok.",
      rules: "Eşleme kuralları",
      noRules: "Kural yok: bu kaynaktan gelen her şey düşer (asla kişisel alana varsayılmaz).",
      ruleType: {
        repo: "Depo kimliği",
        path_prefix: "Yol öneki",
        calendar_binding: "Takvim bağlantısı",
      } satisfies Record<MappingRuleType, string>,
      ruleTypeLabel: "Kural türü",
      rulePattern: "Desen",
      ruleTarget: "Hedef çalışma alanı",
      addRule: "Kural ekle",
      ruleFailed: "Kural kaydedilemedi.",
      ruleEnabled: "Kural açık",
      scrubTerms: "Temizlenecek terimler",
      scrubTermsHint: "Müşteri adları, proje kod adları: saklanmadan önce yer tutucuyla değiştirilir.",
      noScrubTerms: "Terim yok.",
      addTerm: "Terim ekle",
      termLabel: "Terim",
      termWorkspace: "Terimlerin çalışma alanı",
      termFailed: "Terim kaydedilemedi.",
      removeTerm: (term: string) => `“${term}” terimini kaldır`,
      removeTermTitle: "Terim kaldırılsın mı?",
      removeTermDescription: (term: string) => `“${term}” bundan sonra yeni kayıtlarda temizlenmez.`,
      forget: "Bu kaynağı unut",
      forgetTitle: (source: string) => `“${source}” unutulsun mu?`,
      forgetDescription: (source: string, episodes: number, memories: number, proposals: number) =>
        `“${source}” duraklatılır; ${episodes} kayıt çöp kutusuna taşınır, bu kaynaktan türeyen ${memories} hafıza arşivlenir/reddedilir ve bekleyen ${proposals} çekirdek blok önerisi reddedilir. Geri alınabilir; kalıcı silme ayrıca onay ister.`,
      forgetFailed: "Kaynak unutulamadı.",
      forgottenResult: (episodes: number) => `${episodes} kayıt çöp kutusuna taşındı.`,
      restore: "Geri getir",
      restoreTitle: (source: string) => `“${source}” geri getirilsin mi?`,
      restoreDescription:
        "Unutma işlemiyle çöpe taşınan ve süresi dolmamış kayıtlar ile hafızalar eski durumlarına döner. Kaynak duraklatılmış kalır.",
      restoreFailed: "Kaynak geri getirilemedi.",
    },
    coreBlocks: {
      title: "Çekirdek bloklar",
      description:
        "Her sohbetin başına eklenen kısa bloklar. 'self' Friday'yi anlatır ve yalnızca belgelerden otomatik derlenir; diğerlerini sen yazarsın ya da onayladığın öneriler günceller.",
      loadFailed: "Çekirdek bloklar yüklenemedi.",
      label: {
        self: "self — Friday kim",
        developer: "developer — seni anlatır",
        current_focus: "current_focus — şu anki odak",
        active_projects: "active_projects — etkin projeler",
        preferences: "preferences — tercihler",
      } satisfies Record<CoreBlockLabel, string>,
      writer: {
        owner: "sen",
        assembler: "otomatik derleyici",
        approved_proposal: "onaylanan öneri",
      } satisfies Record<CoreBlockWriterKind, string>,
      empty: "Boş",
      version: (version: number) => `Sürüm ${version}`,
      updatedBy: (writer: string, when: string) => `${writer} · ${when}`,
      chars: (used: number, budget: number) => `${used} / ${budget} karakter`,
      overBudget: "Karakter sınırı aşıldı.",
      readOnly: "Salt okunur: bu blok yalnızca belgelerden derlenir.",
      sources: "Kaynak satırları",
      sourcesFailed: "Kaynak satırları yüklenemedi.",
      anchorGroup: {
        identity: "Kimlik",
        rule: "Kural",
        adr: "ADR",
        phase: "Aşama",
      } satisfies Record<SelfAnchorGroup, string>,
      edit: "Düzenle",
      save: "Kaydet",
      cancel: "Vazgeç",
      busy: "Kaydediliyor…",
      saveFailed: "Blok kaydedilemedi.",
      private: "Özel (sohbete eklenmez)",
      history: "Sürüm geçmişi",
      hideHistory: "Geçmişi gizle",
      historyFailed: "Sürüm geçmişi yüklenemedi.",
      noHistory: "Henüz sürüm yok.",
      current: "güncel",
      rollback: "Bu sürüme dön",
      rollbackTitle: (version: number) => `Sürüm ${version} geri yüklensin mi?`,
      rollbackDescription: "Bu, eski içerikle yeni bir sürüm oluşturur; geçmiş silinmez.",
      rollbackFailed: "Geri alma yapılamadı.",
    },
    sessions: {
      title: "Oturumlar",
      description:
        "Claude Code oturumların (kancalardan) ve bu çalışma alanındaki harness işleri. Oturum bilgileri temizlenmiş, kısa özetlerdir.",
      loadFailed: "Oturumlar yüklenemedi.",
      empty: "Kayıtlı oturum yok. Claude Code kancalarını kurmak için docs/runbooks/claude-code-hooks.md.",
      status: {
        running: "Çalışıyor",
        waiting_for_owner: "Seni bekliyor",
        stopped: "Bitti",
        stale: "Sessiz (24 saattir olay yok)",
      } satisfies Record<CodingSessionStatus, string>,
      lastEvent: (event: string, when: string) => `${event} · ${when}`,
      events: (count: number) => `${count} olay`,
      notification: "Son bildirim (araç metni)",
      harness: "Harness işleri",
      harnessEmpty: "Bu çalışma alanında harness işi yok.",
      harnessFailed: "Harness işleri yüklenemedi.",
      harnessAll: "Harness konsolunu aç",
    },
  },
  knowledge: {
    sourceType: {
      pasted_text: "Metin yapıştır",
      file_upload: "Dosya yükle",
      url: "URL'den",
    } satisfies Record<KnowledgeSourceType, string>,
    sensitivity: {
      normal: "Normal",
      private: "Özel",
    } satisfies Record<KnowledgeSensitivity, string>,
  },
  task: {
    priority: {
      low: "Düşük",
      normal: "Normal",
      high: "Yüksek",
    } satisfies Record<TaskPriority, string>,
  },
  token: {
    kind: {
      cli: "CLI",
      openai: "OpenAI uyumlu istemci",
      hook: "Claude Code kancası",
    } satisfies Record<PatKind, string>,
    kindHint: {
      cli: "Friday komut satırı (friday CLI) için.",
      openai:
        "Open WebUI gibi OpenAI uyumlu istemciler için: yalnızca sohbet, araç ve onay yok. CLI uç noktalarında çalışmaz.",
      hook: "friday-hook için (Claude Code oturum olayları): yalnızca olay gönderir, hiçbir şey okuyamaz. Yalnızca kişisel çalışma alanına bağlanır; token'ı macOS Anahtar Zinciri'ne kaydet.",
    } satisfies Record<PatKind, string>,
  },
  // Settings → "Bağlantılar" (Phase 5 branch 9).
  connectors: {
    title: "Bağlantılar",
    description:
      "Friday'nin bu çalışma alanında okuyabildiği ve (her seferinde onayınla) yazabildiği dış sistemler. Bağlantı açmak hiçbir aracı kendiliğinden çalıştırmaz: her araç önce şema doğrulamasından (pin) geçer.",
    loadFailed: "Bağlantılar yüklenemedi.",
    empty: "Bu çalışma alanı türü için tanımlı bağlantı yok.",
    kind: { mcp: "MCP sunucusu", native: "Yerel köprü" } as Record<string, string>,
    dataClass: {
      firm: "Firma verisi",
      personal: "Kişisel veri (yalnızca yerel model)",
    } as Record<string, string>,
    blocked: "Engelli — bu sürümde açılamaz",
    notBindable: "Bu çalışma alanı türünde açılamaz",
    enabled: "Açık",
    disabled: "Kapalı",
    enable: "Aç",
    disable: "Kapat",
    enableTitle: (name: string) => `${name} bağlantısı açılsın mı?`,
    enableDescription: (name: string, personal: boolean) =>
      `${name} bu çalışma alanında açılır. Araçlar şema doğrulaması (pin) geçene kadar kapalı kalır; okumalar denetim kaydına yazılır, her yazma ayrı bir onay ister.` +
      (personal
        ? " Bu kişisel veri içerir: okumalar yalnızca web uygulamasından ve yalnızca kişisel veriye izinli (yerel) modellerle yapılır."
        : ""),
    disableTitle: (name: string) => `${name} bağlantısı kapatılsın mı?`,
    disableDescription: (name: string) =>
      `${name} bu çalışma alanında kapatılır: Friday artık okuma yapmaz; bekleyen yazma onayları çalıştırılmadan başarısız olur.`,
    busy: "Kaydediliyor…",
    toggleFailed: "Bağlantı güncellenemedi.",
    tools: "Araçlar",
    toolKind: { read: "Okuma", write: "Yazma (onaylı)" } as Record<string, string>,
    health: "Sağlık",
    neverChecked: "Henüz kontrol edilmedi.",
    checkedAt: (when: string) => `Son kontrol: ${when}`,
    server: (name: string, version: string) => `Sunucu: ${name}${version ? ` ${version}` : ""}`,
    protocol: (version: string) => `Protokol: ${version}`,
    healthError: (code: string) => `Son kontrol hatası: ${code}`,
    pinNote:
      "Şeması değişen ya da sunucudan kaybolan bir araç, yeni bir manifest commit'i gelene kadar kapalı kalır.",
    recentReads: "Son okumalar",
    recentReadsHint:
      "Denetim kaydından: araç, sonuç, boyut ve süre. Okunan içerik ve argümanlar burada gösterilmez.",
    recentReadsEmpty: "Henüz okuma yok.",
    recentReadsFailed: "Son okumalar yüklenemedi.",
    readOutcome: {
      ok: "Başarılı",
      channel_forbidden: "Bu kanaldan izin yok",
      not_available: "Araç kapalı",
      invalid_arguments: "Geçersiz argüman",
      out_of_scope: "Çalışma alanı dışında",
      tool_error: "Sunucu hata döndü",
      timeout: "Zaman aşımı",
      transport_error: "Bağlantı hatası",
      protocol_error: "Protokol hatası",
      pin_mismatch: "Şema değişti",
      not_configured: "Yapılandırılmamış",
      reconnect_required: "Yeniden bağlanma gerekli",
    } as Record<string, string>,
    columns: {
      when: "Zaman",
      tool: "Araç",
      outcome: "Sonuç",
      size: "Boyut",
      duration: "Süre",
      channel: "Kanal",
    },
    truncated: "kırpıldı",
    personalRead: "kişisel",
    chars: (n: number) => `${n} karakter`,
    ms: (n: number) => `${n} ms`,
    // ConnectorWriteExecution.ErrorType (backend connectors/models.py).
    writeError: {
      connector_changed:
        "Bağlantı ya da araç onaydan sonra değişti; hiçbir şey gönderilmedi.",
      pin_mismatch: "Aracın şeması değişti; hiçbir şey gönderilmedi.",
      not_configured: "Bağlantı sunucuda yapılandırılmamış; hiçbir şey gönderilmedi.",
      timeout: "Zaman aşımı: gönderildi mi bilinmiyor. Yeniden gönderilmez.",
      transport_error: "Bağlantı hatası.",
      protocol_error: "Sunucu beklenmeyen bir yanıt verdi.",
      tool_error: "Dış sistem isteği reddetti.",
      target_missing: "Hedef (ör. cihaz) artık yok; hiçbir şey gönderilmedi.",
      worker_lost:
        "Çalışan süreç gönderim sırasında kayboldu: gönderildi mi bilinmiyor. Yeniden gönderilmez.",
      head_moved: "Dalın ucu onaylanan commit'ten ilerledi; hiçbir şey gönderilmedi.",
      unknown: "Beklenmeyen hata: gönderildi mi bilinmiyor. Yeniden gönderilmez.",
    } as Record<string, string>,
    unknownOutcomeNote:
      "Sonuç bilinmiyor: Friday bu isteği asla yeniden göndermez. Dış sistemde gerçekten olup olmadığını kendin kontrol et.",
  },
  // Firma → "Deploy hedefleri" (Phase 5 branch 9).
  deploys: {
    title: "Deploy hedefleri",
    description:
      "Friday'nin yalnızca senin ayrı onayınla, birleştirilmiş commit'leri dağıtabildiği sunucular. Friday kendi deposunu asla dağıtmaz.",
    loadFailed: "Deploy hedefleri yüklenemedi.",
    empty: "Henüz deploy hedefi yok.",
    emptyHint:
      "Yeni bir hedef kapalı olarak oluşturulur: önce durum kontrolü çalıştır, sonra etkinleştir.",
    newTarget: "Yeni hedef",
    enabled: "Etkin",
    disabled: "Kapalı",
    enable: "Etkinleştir",
    disable: "Devre dışı bırak",
    enableTitle: (name: string) => `${name} hedefi etkinleştirilsin mi?`,
    enableDescription:
      "Etkin bir hedefe deploy istenebilir; her deploy yine ayrı bir onay ister.",
    disableTitle: (name: string) => `${name} hedefi devre dışı bırakılsın mı?`,
    disableDescription: "Devre dışı bir hedefe deploy istenemez; çalışan bir deploy etkilenmez.",
    toggleFailed: "Hedef güncellenemedi.",
    liveSha: "Canlı commit",
    liveUnknown: "Bilinmiyor — durum kontrolü çalıştır",
    lastStatus: (when: string) => `Son durum kontrolü: ${when}`,
    lastStatusError: (code: string) => `Son durum kontrolü hatası: ${code}`,
    refreshStatus: "Durum kontrolü",
    refreshQueued: "Durum kontrolü sıraya alındı.",
    refreshFailed: "Durum kontrolü başlatılamadı.",
    branch: "İzlenen dal",
    destination: "SSH",
    app: "Uygulama",
    repo: "Depo",
    health: "Sağlık",
    healthWindow: (seconds: number) => `${seconds} sn içinde`,
    requireCi: "Başarılı CI şart",
    autoRollback: "Otomatik geri alma",
    yes: "evet",
    no: "hayır",
    busyTarget: "Bu hedefte bir deploy sürüyor.",
    deploy: "Deploy",
    runs: "Çalıştırmalar",
    runsEmpty: "Henüz çalıştırma yok.",
    runsFailed: "Çalıştırmalar yüklenemedi.",
    openRun: "Ayrıntı",
    backToTargets: "Deploy hedefleri",
    runFailed: "Bu çalıştırma yüklenemedi.",
    runTitle: (target: string, sha: string) => `${target} · ${sha}`,
    sha: "Commit",
    previousSha: "Önceki commit",
    rollbackSha: "Geri alma commit'i",
    startedAt: "Dağıtım başladı",
    finishedAt: "Bitti",
    errorCode: "Neden",
    healthChecks: "Sağlık kontrolleri",
    healthCheckPass: "geçti",
    healthCheckFail: "başarısız",
    healthPlan: (checks: number, window: number, fail: number) =>
      `${window} sn içinde ${checks} kontrol; art arda ${fail} başarısızlık başarısız sayılır.`,
    approval: "Onay isteği",
    output: "Adım günlüğü",
    showOutput: "Uzak sunucu çıktısını göster",
    hideOutput: "Uzak sunucu çıktısını gizle",
    outputWarning:
      "Bu, uzak sunucudaki betiğin kendi çıktısıdır (kısaltılmış, gizli bilgiler ayıklanmış) ve güvenilmeyen veri olarak ele alınmalıdır. Durum ve neden alanları Friday'nin kendi kaydıdır.",
    noOutput: "Çıktı yok.",
    cancel: "İptal et",
    cancelTitle: "Bu deploy iptal edilsin mi?",
    cancelDescription:
      "Deploy komutu henüz gönderilmediyse çalıştırma iptal edilir ve hedefe dokunulmaz. Komut gönderildiyse iptal reddedilir.",
    cancelConfirm: "Evet, iptal et",
    cancelBusy: "İptal ediliyor…",
    cancelFailed: "Deploy iptal edilemedi.",
    // The "Deploy" dialog.
    dialog: {
      title: (target: string) => `${target} için deploy iste`,
      description:
        "Bu bir onay isteği oluşturur; deploy ancak Onaylar sayfasında ayrıca karar verdiğinde çalışır.",
      shaLabel: "Commit SHA",
      shaHint: (branch: string) =>
        `“${branch}” dalındaki birleştirilmiş bir commit (7–40 onaltılık karakter).`,
      prefilled: (branch: string) => `“${branch}” dalının ucu GitHub'dan dolduruldu.`,
      manual: "GitHub bağlı değil ya da okunamadı: SHA'yı kendin yaz; CI durumu bilinmiyor.",
      loadingHead: "Dal ucu okunuyor…",
      commitTitle: "Commit başlığı (depodan gelen metin)",
      ciRequired: "Bu hedef başarılı CI ister; CI başarılı değilse istek reddedilir.",
      shaInvalid: "Geçerli bir commit SHA'sı gir (7–40 onaltılık karakter).",
      shaIsLive: "Bu commit zaten canlıda.",
      submit: "Devam et",
      confirmTitle: "Deploy isteği oluşturulsun mu?",
      confirmDescription: (sha: string, target: string) =>
        `${target} hedefine “${sha}” için bir deploy onay isteği oluşturulur. Hiçbir şey hemen dağıtılmaz.`,
      confirm: "Onay isteği oluştur",
      busy: "Oluşturuluyor…",
      failed: "Deploy isteği oluşturulamadı.",
      created: "Deploy onay isteği oluşturuldu.",
      openApproval: "Onaylar sayfasında karar ver",
    },
    // The target form.
    form: {
      title: "Yeni deploy hedefi",
      name: "Ad",
      environment: "Ortam",
      sshHost: "SSH sunucusu",
      sshPort: "SSH portu",
      sshUser: "SSH kullanıcısı",
      hostKey: "Sunucu açık anahtarı (known_hosts satırı)",
      hostKeyHint: "Sabitlenir: ilk bağlantıda güvenme yoktur.",
      remoteApp: "Uzak uygulama adı",
      repo: "Depo git adresi",
      branch: "İzlenen dal",
      healthUrl: "Sağlık adresi (isteğe bağlı)",
      healthTimeout: "Sağlık penceresi (saniye)",
      requireCi: "Başarılı CI şart",
      autoRollback: "Sağlık başarısız olursa önceki commit'e otomatik geri al",
      submit: "Hedefi oluştur",
      busy: "Oluşturuluyor…",
      failed: "Hedef oluşturulamadı.",
      createdDisabled: "Hedef kapalı olarak oluşturuldu. Önce durum kontrolü çalıştır.",
    },
    // A finished run's banner, per state (a degraded deploy must never look clean).
    banner: {
      succeeded: "Deploy başarılı: yeni commit canlıda ve sağlık kontrolünü geçti.",
      succeeded_degraded:
        "Kısmen başarılı — temiz değil: uygulama ayakta ama sağlık adresi (health_url) kontrolü başarısız oldu. Uygulamayı kontrol et.",
      rolled_back: (sha: string) =>
        `Geri alındı: sağlık kontrolü başarısız oldu ve Friday hedefi önceki commit'e (“${sha}”) döndürdü. Yeni commit canlıda DEĞİL.`,
      rollback_failed:
        "Geri alma BAŞARISIZ: sağlık kontrolü başarısız oldu ve önceki commit'e dönülemedi. Hedef bozuk durumda olabilir — hemen elle kontrol et, ardından durum kontrolü çalıştır.",
      health_failed:
        "Sağlık kontrolü başarısız: yeni commit canlıda ama sağlıklı görünmüyor. Otomatik geri alma kapalı; hiçbir şey geri alınmadı. Geri dönmek için yeni bir deploy isteği gerekir.",
      unknown_outcome:
        "Sonuç bilinmiyor: çalıştırıcıyla bağlantı dağıtım sırasında koptu. Friday hiçbir şeyi yeniden göndermez; hedefin gerçek durumunu görmek için durum kontrolü çalıştır.",
      failed: "Deploy yapılmadı: çalıştırıcı deploy komutundan önce durdu. Hedef değişmedi.",
      refused: "Deploy reddedildi: onay anında hedefe hiç dokunulmadı.",
      cancelled: "Deploy iptal edildi; hedefe dokunulmadı.",
      active: "Deploy sürüyor…",
      runnerOffline:
        "Çalıştırıcı (Mac) çevrimdışıysa deploy, çalıştırıcı geri gelene kadar bekler.",
    },
    // DeployRun.ErrorCode (backend deploys/models.py).
    errorCodes: {
      target_busy: "Hedefte başka bir deploy sürüyor.",
      target_changed: "Hedefin yapılandırması onaydan sonra değişti.",
      target_disabled: "Hedef devre dışı.",
      self_repo_unconfigured: "Kendi-depo koruması yapılandırılmamış.",
      ssh_key_missing: "Çalıştırıcıda SSH anahtarı yok.",
      ssh_failed: "SSH bağlantısı kurulamadı.",
      status_failed: "Durum komutu başarısız.",
      status_unparseable: "Durum çıktısı okunamadı.",
      repo_mismatch: "Uzak sunucudaki depo hedefle eşleşmiyor.",
      self_repo: "Friday'nin kendi deposu dağıtılamaz.",
      target_drifted: "Canlı commit beklenenden farklı (sapma); hiçbir şey dağıtılmadı.",
      deploy_refused: "Uzak sunucu deploy'u reddetti.",
      remote_busy: "Uzak sunucu meşgul.",
      remote_unavailable: "Uzak sunucuya ulaşılamadı.",
      build_failed: "Derleme başarısız.",
      deploy_command_failed: "Deploy komutu başarısız.",
      health_url_failed: "Sağlık adresi kontrolü başarısız.",
      health_checks_failed: "Sağlık kontrolleri art arda başarısız.",
      health_window_elapsed: "Sağlık penceresi art arda iki başarılı kontrol olmadan doldu.",
      rollback_command_failed: "Geri alma komutu başarısız.",
      rollback_outcome_unknown: "Geri almanın sonucu bilinmiyor.",
      rollback_health_failed: "Geri alma sonrası sağlık kontrolü başarısız.",
      runner_lost: "Çalıştırıcı çalışma sırasında kayboldu.",
      unexpected_error: "Beklenmeyen hata.",
    } as Record<string, string>,
  },
  // Harness implementation detail: "PR aç" + PR/CI panel (Phase 5 branch 9).
  pullRequest: {
    title: "Pull request",
    loadFailed: "PR durumu yüklenemedi.",
    open: "PR aç",
    branches: (head: string, base: string) => `${head} → ${base}`,
    notReady: "PR yalnızca başarıyla tamamlanıp dalı gönderilmiş işler için açılabilir.",
    dialogTitle: "PR açma isteği oluşturulsun mu?",
    dialogDescription:
      "Bu bir onay isteği oluşturur; PR ancak Onaylar sayfasında ayrıca karar verdiğinde GitHub'da açılır. Friday PR'ı birleştirmez.",
    titleLabel: "PR başlığı",
    titleHint: "Boş bırakırsan işin hedefinin ilk satırı kullanılır.",
    confirm: "Onay isteği oluştur",
    busy: "Oluşturuluyor…",
    failed: "PR isteği oluşturulamadı.",
    request: "PR onay isteği",
    decide: "Onaylar sayfasında karar ver",
    viewRequest: "Onay isteğini gör",
    opened: (number: number | null) => (number ? `PR #${number} açıldı` : "PR açıldı"),
    openOnGithub: "GitHub'da aç",
    ci: "CI",
    ciUnavailable: "CI durumu okunamadı",
    noCi: "CI durumu yok (GitHub bağlı değil ya da iş henüz tamamlanmadı).",
  },
} as const;
