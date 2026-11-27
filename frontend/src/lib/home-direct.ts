/**
 * Phase 13b D7 (ADR-018): the closed reasons a Home Assistant card was not
 * a direct call, and the ones the card shows. The texts mirror
 * `connectors.home_direct.REASON_TEXT` (backend); only the reasons that can
 * concern a flagged entity -- or an eligible media player not flagged yet
 * (`not_flagged`) -- are shown (the recommendation recorded in the 1c owner
 * steps). `not_media_player`, `service_not_direct`, `origin_not_direct` and
 * the like are expected for an ordinary card and stay silent.
 */
export const DIRECT_REASON_SHOWN: Readonly<Record<string, string>> = {
  not_flagged: "işaretli değil",
  kill_switch_env: "doğrudan medya ortam ayarıyla kapalı",
  kill_switch_owner: "doğrudan medya kapalı",
  rate_limited: "dakikalık sınır aşıldı",
  rate_store_unavailable: "sınır sayacı şu an kullanılamıyor",
  volume_unknown: "güncel ses düzeyi bilinmiyor",
  volume_at_max: "ses en yüksek düzeye yakın",
  not_available: "Home Assistant şu an kullanılamıyor",
  provenance_mismatch: "istek söylediğinle eşleşmiyor",
  target_unavailable: "cihaz şu an doğrulanamadı",
};
