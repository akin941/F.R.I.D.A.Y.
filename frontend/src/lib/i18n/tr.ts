import type {
  AgentFailureReason,
  AgentStatus,
  AgentStepKind,
  AgentStepStatus,
  ApprovalOrigin,
  ApprovalRequestedChannel,
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
      trash: "Çöp kutusu",
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
      connector_local_model_required:
        "Bu kaynak kişisel veri içeriyor; okumak için kişisel veriye izinli yerel bir model gerekiyor. Hiçbir şey okunmadı.",
      connector_invalid_arguments:
        "İstek bu kaynak için anlaşılamadı ya da bu çalışma alanının dışında kalıyor. Hiçbir şey okunmadı.",
      connector_read_failed: "Kaynak şu anda okunamadı. Biraz sonra tekrar dene.",
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
        `“${title}” arşivlenir ve Jarvis onu artık kullanmaz. Arşivden istediğin zaman geri yükleyebilirsin.`,
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
        "Bu çalışma alanına bağlı Telegram sohbeti artık Jarvis'e mesaj iletemez. Yeniden bağlamak için yeni bir bağlantı kodu gerekir.",
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
      ownSummaryNote: "Özet ve bulgular Jarvis'in kendi özetidir (model üretimi).",
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
      selfRepo: "Jarvis'in kendi deposu",
      selfRepoHint:
        "Bu depo Jarvis'in kendi kurulumuna çözülüyor: kendini değiştirme koruması (ADR-004) uygulama işlerini reddeder. Salt okunur keşif çalışır.",
      selfRepoUnconfigured:
        "HARNESS_SELF_REPO_IDENTIFIERS tanımlı değil: kendini değiştirme koruması hangi deponun Jarvis olduğunu bilemediği için tüm uygulama işlerini reddeder. Keşif işleri çalışır.",
    },
  },
  approvals: {
    // Human title per tool; a tool without one keeps its raw `tool_name`.
    toolTitles: {
      harness_implement: "Uygulama planı onayı (Kapı A)",
      harness_push: "Gönderim onayı (Kapı B)",
      vault_export: "Obsidian vault dışa aktarımı",
      purge_trash: "Çöp kutusundan kalıcı silme",
    } as Record<string, string>,
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
    } satisfies Record<PatKind, string>,
    kindHint: {
      cli: "Jarvis komut satırı (jarvis CLI) için.",
      openai:
        "Open WebUI gibi OpenAI uyumlu istemciler için: yalnızca sohbet, araç ve onay yok. CLI uç noktalarında çalışmaz.",
    } satisfies Record<PatKind, string>,
  },
} as const;
