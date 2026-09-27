import type {
  AgentFailureReason,
  AgentStatus,
  AgentStepKind,
  AgentStepStatus,
  ApprovalStatus,
  HarnessAgent,
  HarnessJobStatus,
  ImplFailureReason,
  ImplStatus,
  KnowledgeSensitivity,
  KnowledgeSourceType,
  KnowledgeStatus,
  MessageChannel,
  MemorySensitivity,
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
  // Agents
  "agent.permission_denied": { message: "Rolün bu ajan işlemine yetmiyor.", specific: true },
  "agent.invalid_transition": {
    message: "Ajan ya da adım artık bu işleme uygun değil.",
    specific: true,
  },
  // Harness
  "harness.permission_denied": { message: "Rolün bu harness işlemine yetmiyor.", specific: true },
  "harness.invalid_transition": {
    message: "Bu harness işi, deposu ya da ajanı artık bu işleme uygun değil.",
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
      workspaces: "Firma dosyası",
      agents: "Ajanlar",
      devices: "Cihazlar",
      integrations: "Entegrasyonlar",
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
    emptyConversation: "Jarvis'e bir şey sor.",
    unavailable: "Bu sohbete artık erişilemiyor.",
    placeholder: "Jarvis'e yaz…",
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
    } as Record<string, string>,
    streamFailed: "Asistan beklenmedik bir hatayla karşılaştı. Lütfen tekrar deneyin.",
    private: "Özel",
    privateHint: "Özel sohbet: bu sohbetten hafıza çıkarılmaz.",
    channel: {
      web: "Web",
      cli: "CLI",
      telegram: "Telegram",
    } satisfies Record<MessageChannel, string> as Record<string, string>,
    viaChannel: (label: string) => `${label} üzerinden`,
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
  },
  approvals: {
    // Human title per tool; a tool without one keeps its raw `tool_name`.
    toolTitles: {
      harness_implement: "Uygulama planı onayı (Kapı A)",
      harness_push: "Gönderim onayı (Kapı B)",
    } as Record<string, string>,
    consequence: {
      harness_implement: {
        approve:
          "Onaylarsan ajan bu plana göre ayrı bir çalışma ağacında değişiklik yapmaya başlar. Dal, ayrı bir gönderim onayı (Kapı B) olmadan uzak depoya gönderilmez.",
        reject:
          "Reddedersen uygulama işi başarısız olarak kapanır; depoda hiçbir değişiklik yapılmaz.",
      },
      harness_push: {
        approve: "Onaylarsan bu dal uzak depoya gönderilir (push). Gönderim geri alınamaz.",
        reject: "Reddedersen değişiklikler gönderilmez ve uygulama işi başarısız olarak kapanır.",
      },
      generic: {
        approve: "Onaylarsan bu istek çalıştırılır.",
        reject: "Reddedersen bu istek çalıştırılmaz.",
      },
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
} as const;
