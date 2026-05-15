// ============================================================
//  Bot de Ponto — Google Apps Script Backend
//  Cole todo este arquivo em um novo projeto Apps Script,
//  configure a constante SECRET_KEY e publique como Web App
//  (Executar como: Eu mesmo | Quem tem acesso: Qualquer pessoa)
// ============================================================

const SECRET_KEY = "sua_chave_secreta_aqui"; // deve coincidir com APPSCRIPT_SECRET no .env

// Índices das colunas da aba Registros (base 0)
const COL = {
  THREAD_ID:         0,
  USER_ID:           1,
  USER_NAME:         2,
  DATE:              3,
  INICIO:            4,
  FIM:               5,
  HORAS_TRABALHADAS: 6,
  META_HORAS:        7,
  STATUS:            8,
  JUSTIFICATIVA:     9,
  PAUSAS_JSON:       10,
};

// ── Entry point ──────────────────────────────────────────────────────────────

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);

    if (body.secret !== SECRET_KEY) {
      return jsonResp({ success: false, error: "Não autorizado" });
    }

    const handlers = {
      register_session: registerSession,
      get_active_thread: getActiveThread,
      get_status:        getStatus,
      start:             startSession,
      pause:             pauseSession,
      resume:            resumeSession,
      close:             closeSession,
      justify:           justifySession,
      get_all_users:     getAllUsers,
      set_meta:          setMeta,
      get_records:       getRecords,
    };

    const handler = handlers[body.action];
    if (!handler) {
      return jsonResp({ success: false, error: `Ação desconhecida: ${body.action}` });
    }

    return jsonResp(handler(body));
  } catch (err) {
    return jsonResp({ success: false, error: err.toString() });
  }
}

function jsonResp(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

// ── Sheet helpers ─────────────────────────────────────────────────────────────

function getOrCreateSheet(name, headers) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(name);
  if (!sheet) {
    sheet = ss.insertSheet(name);
    if (headers) sheet.appendRow(headers);
  }
  return sheet;
}

function getRegistros() {
  return getOrCreateSheet("Registros", [
    "thread_id","user_id","user_name","date","inicio","fim",
    "horas_trabalhadas","meta_horas","status","justificativa","pausas_json",
  ]);
}

function getUsuarios() {
  return getOrCreateSheet("Usuarios", [
    "user_id","user_name","meta_horas","data_registro",
  ]);
}

function getConfig() {
  const sheet = getOrCreateSheet("Config", ["chave","valor"]);
  const data  = sheet.getDataRange().getValues();
  const cfg   = {};
  data.forEach(row => { cfg[row[0]] = row[1]; });
  return cfg;
}

// ── Lookup helpers ────────────────────────────────────────────────────────────

function findRegistroByThreadId(threadId) {
  const sheet = getRegistros();
  const data  = sheet.getDataRange().getValues();
  for (let i = 1; i < data.length; i++) {
    if (String(data[i][COL.THREAD_ID]) === String(threadId)) {
      return { row: i + 1, data: data[i] };
    }
  }
  return null;
}

function findActiveRegistroByUserId(userId, date) {
  const sheet  = getRegistros();
  const data   = sheet.getDataRange().getValues();
  const closed = new Set(["fechado", "justificado"]);
  for (let i = 1; i < data.length; i++) {
    if (
      String(data[i][COL.USER_ID]) === String(userId) &&
      String(data[i][COL.DATE])    === String(date) &&
      !closed.has(data[i][COL.STATUS])
    ) {
      return { row: i + 1, data: data[i] };
    }
  }
  return null;
}

function getUserMeta(userId) {
  const sheet = getUsuarios();
  const data  = sheet.getDataRange().getValues();
  const cfg   = getConfig();
  const def   = Number(cfg["default_meta"] || 8);
  for (let i = 1; i < data.length; i++) {
    if (String(data[i][0]) === String(userId)) {
      return Number(data[i][2]) || def;
    }
  }
  return def;
}

function upsertUser(userId, userName) {
  const sheet = getUsuarios();
  const data  = sheet.getDataRange().getValues();
  for (let i = 1; i < data.length; i++) {
    if (String(data[i][0]) === String(userId)) {
      if (data[i][1] !== userName) sheet.getRange(i + 1, 2).setValue(userName);
      return;
    }
  }
  const cfg = getConfig();
  sheet.appendRow([userId, userName, Number(cfg["default_meta"] || 8), new Date().toISOString()]);
}

// ── Hour calculation ──────────────────────────────────────────────────────────

function calcHours(inicio, fim, pausasJson) {
  if (!inicio) return 0;
  const start  = new Date(inicio).getTime();
  const end    = fim ? new Date(fim).getTime() : Date.now();
  let   total  = end - start;

  try {
    const pausas = JSON.parse(pausasJson || "[]");
    pausas.forEach(p => {
      const ps = new Date(p.inicio).getTime();
      const pe = p.fim ? new Date(p.fim).getTime() : end;
      total   -= (pe - ps);
    });
  } catch (_) {}

  return Math.max(0, total / 3_600_000);
}

// ── Action handlers ───────────────────────────────────────────────────────────

function registerSession({ thread_id, user_id, user_name, date }) {
  const existing = findRegistroByThreadId(thread_id);
  if (existing) {
    return {
      success:      true,
      meta_horas:   Number(existing.data[COL.META_HORAS]),
      status:       existing.data[COL.STATUS],
      already_exists: true,
    };
  }

  upsertUser(user_id, user_name);
  const meta = getUserMeta(user_id);

  getRegistros().appendRow([
    thread_id, user_id, user_name, date,
    "", "", 0, meta, "pendente", "", "[]",
  ]);

  return { success: true, meta_horas: meta, status: "pendente" };
}

function getActiveThread({ user_id, date }) {
  const r = findActiveRegistroByUserId(user_id, date);
  return {
    success:   true,
    thread_id: r ? String(r.data[COL.THREAD_ID]) : null,
  };
}

function getStatus({ thread_id }) {
  const r = findRegistroByThreadId(thread_id);
  if (!r) return { success: false, error: "Sessão não encontrada" };

  const d = r.data;
  const horas = d[COL.STATUS] === "ativo"
    ? calcHours(d[COL.INICIO], null, d[COL.PAUSAS_JSON])
    : Number(d[COL.HORAS_TRABALHADAS]);

  return {
    success:           true,
    thread_id:         String(d[COL.THREAD_ID]),
    user_id:           String(d[COL.USER_ID]),
    user_name:         d[COL.USER_NAME],
    date:              d[COL.DATE],
    inicio:            d[COL.INICIO]  || null,
    fim:               d[COL.FIM]     || null,
    horas_trabalhadas: horas,
    meta_horas:        Number(d[COL.META_HORAS]),
    status:            d[COL.STATUS],
    justificativa:     d[COL.JUSTIFICATIVA] || null,
  };
}

function startSession({ thread_id }) {
  const r = findRegistroByThreadId(thread_id);
  if (!r)                          return { success: false, error: "Sessão não encontrada" };
  if (r.data[COL.STATUS] !== "pendente") return { success: false, error: "Ponto já foi iniciado" };

  const now   = new Date().toISOString();
  const sheet = getRegistros();
  sheet.getRange(r.row, COL.INICIO  + 1).setValue(now);
  sheet.getRange(r.row, COL.STATUS  + 1).setValue("ativo");

  return {
    success:           true,
    inicio:            now,
    horas_trabalhadas: 0,
    meta_horas:        Number(r.data[COL.META_HORAS]),
    status:            "ativo",
    user_id:           String(r.data[COL.USER_ID]),
  };
}

function pauseSession({ thread_id }) {
  const r = findRegistroByThreadId(thread_id);
  if (!r)                           return { success: false, error: "Sessão não encontrada" };
  if (r.data[COL.STATUS] !== "ativo") return { success: false, error: "Ponto não está ativo" };

  const now    = new Date().toISOString();
  const pausas = JSON.parse(r.data[COL.PAUSAS_JSON] || "[]");
  pausas.push({ inicio: now, fim: null });

  const horas  = calcHours(r.data[COL.INICIO], null, JSON.stringify(pausas));
  const sheet  = getRegistros();
  sheet.getRange(r.row, COL.STATUS      + 1).setValue("pausado");
  sheet.getRange(r.row, COL.PAUSAS_JSON + 1).setValue(JSON.stringify(pausas));
  sheet.getRange(r.row, COL.HORAS_TRABALHADAS + 1).setValue(horas);

  return {
    success:           true,
    inicio:            r.data[COL.INICIO],
    horas_trabalhadas: horas,
    meta_horas:        Number(r.data[COL.META_HORAS]),
    status:            "pausado",
    user_id:           String(r.data[COL.USER_ID]),
  };
}

function resumeSession({ thread_id }) {
  const r = findRegistroByThreadId(thread_id);
  if (!r)                             return { success: false, error: "Sessão não encontrada" };
  if (r.data[COL.STATUS] !== "pausado") return { success: false, error: "Ponto não está pausado" };

  const now    = new Date().toISOString();
  const pausas = JSON.parse(r.data[COL.PAUSAS_JSON] || "[]");
  for (let i = pausas.length - 1; i >= 0; i--) {
    if (!pausas[i].fim) { pausas[i].fim = now; break; }
  }

  const horas = calcHours(r.data[COL.INICIO], null, JSON.stringify(pausas));
  const sheet = getRegistros();
  sheet.getRange(r.row, COL.STATUS            + 1).setValue("ativo");
  sheet.getRange(r.row, COL.PAUSAS_JSON       + 1).setValue(JSON.stringify(pausas));
  sheet.getRange(r.row, COL.HORAS_TRABALHADAS + 1).setValue(horas);

  return {
    success:           true,
    inicio:            r.data[COL.INICIO],
    horas_trabalhadas: horas,
    meta_horas:        Number(r.data[COL.META_HORAS]),
    status:            "ativo",
    user_id:           String(r.data[COL.USER_ID]),
  };
}

function closeSession({ thread_id }) {
  const r = findRegistroByThreadId(thread_id);
  if (!r) return { success: false, error: "Sessão não encontrada" };

  const status = r.data[COL.STATUS];
  if (status !== "ativo" && status !== "pausado") {
    return { success: false, error: "Ponto não está aberto" };
  }

  const now    = new Date().toISOString();
  let   pausas = JSON.parse(r.data[COL.PAUSAS_JSON] || "[]");

  if (status === "pausado") {
    for (let i = pausas.length - 1; i >= 0; i--) {
      if (!pausas[i].fim) { pausas[i].fim = now; break; }
    }
  }

  const horas    = calcHours(r.data[COL.INICIO], now, JSON.stringify(pausas));
  const meta     = Number(r.data[COL.META_HORAS]);
  const newStatus = horas >= meta ? "fechado" : "incompleto";
  const sheet    = getRegistros();

  sheet.getRange(r.row, COL.FIM               + 1).setValue(now);
  sheet.getRange(r.row, COL.HORAS_TRABALHADAS + 1).setValue(horas);
  sheet.getRange(r.row, COL.STATUS            + 1).setValue(newStatus);
  sheet.getRange(r.row, COL.PAUSAS_JSON       + 1).setValue(JSON.stringify(pausas));

  return {
    success:           true,
    inicio:            r.data[COL.INICIO],
    fim:               now,
    horas_trabalhadas: horas,
    meta_horas:        meta,
    status:            newStatus,
    user_id:           String(r.data[COL.USER_ID]),
  };
}

function justifySession({ thread_id, justificativa }) {
  const r = findRegistroByThreadId(thread_id);
  if (!r) return { success: false, error: "Sessão não encontrada" };
  if (r.data[COL.STATUS] !== "incompleto") {
    return { success: false, error: "Justificativa só é necessária em sessões incompletas" };
  }

  const sheet = getRegistros();
  sheet.getRange(r.row, COL.STATUS       + 1).setValue("justificado");
  sheet.getRange(r.row, COL.JUSTIFICATIVA + 1).setValue(justificativa);

  return {
    success:           true,
    inicio:            r.data[COL.INICIO],
    horas_trabalhadas: Number(r.data[COL.HORAS_TRABALHADAS]),
    meta_horas:        Number(r.data[COL.META_HORAS]),
    status:            "justificado",
    user_id:           String(r.data[COL.USER_ID]),
  };
}

function getAllUsers(_body) {
  const usuariosData  = getUsuarios().getDataRange().getValues();
  const registrosData = getRegistros().getDataRange().getValues();
  const today         = Utilities.formatDate(new Date(), "GMT-3", "yyyy-MM-dd");

  const users = [];
  for (let i = 1; i < usuariosData.length; i++) {
    const userId   = String(usuariosData[i][0]);
    const userName = usuariosData[i][1];
    const meta     = Number(usuariosData[i][2]);

    let todayRecord = null;
    for (let j = 1; j < registrosData.length; j++) {
      if (
        String(registrosData[j][COL.USER_ID]) === userId &&
        String(registrosData[j][COL.DATE])    === today
      ) {
        todayRecord = registrosData[j];
        break;
      }
    }

    const todayHoras = todayRecord
      ? (todayRecord[COL.STATUS] === "ativo"
          ? calcHours(todayRecord[COL.INICIO], null, todayRecord[COL.PAUSAS_JSON])
          : Number(todayRecord[COL.HORAS_TRABALHADAS]))
      : 0;

    users.push({
      user_id:      userId,
      user_name:    userName,
      meta_horas:   meta,
      today_status: todayRecord ? todayRecord[COL.STATUS] : "ausente",
      today_horas:  todayHoras,
    });
  }

  return { success: true, users };
}

function setMeta({ user_id, meta_horas }) {
  const sheet = getUsuarios();
  const data  = sheet.getDataRange().getValues();
  for (let i = 1; i < data.length; i++) {
    if (String(data[i][0]) === String(user_id)) {
      sheet.getRange(i + 1, 3).setValue(Number(meta_horas));
      return { success: true };
    }
  }
  return { success: false, error: "Usuário não encontrado. O usuário precisa ter clicado em Registro ao menos uma vez." };
}

function getRecords({ user_id, limit = 30 }) {
  const data    = getRegistros().getDataRange().getValues();
  const records = [];

  for (let i = data.length - 1; i >= 1 && records.length < limit; i--) {
    if (String(data[i][COL.USER_ID]) !== String(user_id)) continue;
    records.push({
      thread_id:         String(data[i][COL.THREAD_ID]),
      date:              data[i][COL.DATE],
      inicio:            data[i][COL.INICIO]        || null,
      fim:               data[i][COL.FIM]            || null,
      horas_trabalhadas: Number(data[i][COL.HORAS_TRABALHADAS]),
      meta_horas:        Number(data[i][COL.META_HORAS]),
      status:            data[i][COL.STATUS],
      justificativa:     data[i][COL.JUSTIFICATIVA]  || null,
    });
  }

  return { success: true, records };
}

// ── One-time setup (run manually in Apps Script IDE) ──────────────────────────

function setupSheets() {
  getRegistros();
  getUsuarios();
  const config = getOrCreateSheet("Config", ["chave","valor"]);
  if (config.getLastRow() <= 1) {
    config.appendRow(["default_meta", 8]);
    config.appendRow(["secret_key",   SECRET_KEY]);
  }
  SpreadsheetApp.getUi().alert("Planilhas criadas com sucesso!");
}
