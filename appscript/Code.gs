// ============================================================
//  Bot de Ponto — Google Apps Script Backend
//  Cole todo este arquivo em um novo projeto Apps Script,
//  publique como Web App
//  (Executar como: Eu mesmo | Quem tem acesso: Qualquer pessoa)
// ============================================================

const TZ = "America/Sao_Paulo";

// Registros columns (0-based)
const COL = {
  THREAD_ID:           0,
  USER_ID:             1,
  USER_NAME:           2,
  WEEK_START:          3,  // domingo da semana (YYYY-MM-DD)
  WEEK_END:            4,  // sábado da semana  (YYYY-MM-DD)
  HORAS_ACUMULADAS:    5,  // horas de sessões já encerradas
  META_HORAS:          6,
  STATUS:              7,  // aberto|ativo|pausado|fechado|incompleto|justificado
  JUSTIFICATIVA:       8,
  SESSION_INICIO:      9,  // início da sessão atual (ISO)
  SESSION_PAUSAS_JSON: 10, // pausas da sessão atual (JSON)
};

// ── Entry point ──────────────────────────────────────────────────────────────

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);

    const handlers = {
      register_session:  registerSession,
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
      return jsonResp({ success: false, error: "Ação desconhecida: " + body.action });
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
    "thread_id","user_id","user_name","week_start","week_end",
    "horas_acumuladas","meta_horas","status","justificativa",
    "session_inicio","session_pausas_json",
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
  data.forEach(function(row) { cfg[row[0]] = row[1]; });
  return cfg;
}

// ── Data/semana helpers ───────────────────────────────────────────────────────

function getWeekBounds() {
  const todayStr = Utilities.formatDate(new Date(), TZ, "yyyy-MM-dd");
  const parts    = todayStr.split("-");
  // cria data local para obter dia da semana correto
  const today    = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]));
  const dow      = today.getDay(); // 0=Dom, 6=Sáb

  const sunday = new Date(today);
  sunday.setDate(today.getDate() - dow);

  const saturday = new Date(sunday);
  saturday.setDate(sunday.getDate() + 6);

  const fmt = function(d) {
    return d.getFullYear() + "-"
      + String(d.getMonth() + 1).padStart(2, "0") + "-"
      + String(d.getDate()).padStart(2, "0");
  };

  return {
    start:      fmt(sunday),
    end:        fmt(saturday),
    today:      todayStr,
    isSaturday: dow === 6,
  };
}

// ── Lookup helpers ────────────────────────────────────────────────────────────

function findRegistroByThreadId(threadId) {
  const sheet = getRegistros();
  const data  = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][COL.THREAD_ID]) === String(threadId)) {
      return { row: i + 1, data: data[i] };
    }
  }
  return null;
}

function findRegistroByWeek(userId, weekStart) {
  const sheet = getRegistros();
  const data  = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (
      String(data[i][COL.USER_ID])    === String(userId) &&
      String(data[i][COL.WEEK_START]) === String(weekStart)
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
  const def   = Number(cfg["default_meta"] || 5);
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0]) === String(userId)) {
      return Number(data[i][2]) || def;
    }
  }
  return def;
}

function upsertUser(userId, userName) {
  const sheet = getUsuarios();
  const data  = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0]) === String(userId)) {
      if (data[i][1] !== userName) sheet.getRange(i + 1, 2).setValue(userName);
      return;
    }
  }
  const cfg = getConfig();
  sheet.appendRow([userId, userName, Number(cfg["default_meta"] || 5), new Date().toISOString()]);
}

// ── Cálculo de horas ──────────────────────────────────────────────────────────

function calcSessionHours(inicio, fim, pausasJson) {
  if (!inicio) return 0;
  const start = new Date(inicio).getTime();
  const end   = fim ? new Date(fim).getTime() : Date.now();
  var   total = end - start;

  try {
    var pausas = JSON.parse(pausasJson || "[]");
    for (var i = 0; i < pausas.length; i++) {
      var ps = new Date(pausas[i].inicio).getTime();
      var pe = pausas[i].fim ? new Date(pausas[i].fim).getTime() : end;
      total -= (pe - ps);
    }
  } catch (_) {}

  return Math.max(0, total / 3600000);
}

// ── Handlers ──────────────────────────────────────────────────────────────────

function registerSession(body) {
  var thread_id  = body.thread_id;
  var user_id    = body.user_id;
  var user_name  = body.user_name;
  var week_start = body.week_start;
  var week_end   = body.week_end;

  var existing = findRegistroByThreadId(thread_id);
  if (existing) {
    var d = existing.data;
    return {
      success:        true,
      meta_horas:     Number(d[COL.META_HORAS]),
      status:         d[COL.STATUS],
      week_start:     d[COL.WEEK_START],
      week_end:       d[COL.WEEK_END],
      already_exists: true,
    };
  }

  upsertUser(user_id, user_name);
  var meta = getUserMeta(user_id);

  getRegistros().appendRow([
    thread_id, user_id, user_name, week_start, week_end,
    0, meta, "aberto", "", "", "[]",
  ]);

  return { success: true, meta_horas: meta, status: "aberto", week_start: week_start, week_end: week_end };
}

function getActiveThread(body) {
  var r = findRegistroByWeek(body.user_id, body.week_start);
  var closed = { "fechado": true, "incompleto": true, "justificado": true };
  if (r && !closed[r.data[COL.STATUS]]) {
    return { success: true, thread_id: String(r.data[COL.THREAD_ID]) };
  }
  return { success: true, thread_id: null };
}

function getStatus(body) {
  var r = findRegistroByThreadId(body.thread_id);
  if (!r) return { success: false, error: "Sessão não encontrada" };

  var d      = r.data;
  var status = d[COL.STATUS];
  var sessionH = (status === "ativo" || status === "pausado")
    ? calcSessionHours(d[COL.SESSION_INICIO], null, d[COL.SESSION_PAUSAS_JSON])
    : 0;
  var horasSemana = Number(d[COL.HORAS_ACUMULADAS]) + sessionH;

  return {
    success:        true,
    thread_id:      String(d[COL.THREAD_ID]),
    user_id:        String(d[COL.USER_ID]),
    user_name:      d[COL.USER_NAME],
    week_start:     d[COL.WEEK_START],
    week_end:       d[COL.WEEK_END],
    horas_semana:   horasSemana,
    meta_horas:     Number(d[COL.META_HORAS]),
    status:         status,
    session_inicio: d[COL.SESSION_INICIO] || null,
    justificativa:  d[COL.JUSTIFICATIVA]  || null,
  };
}

function startSession(body) {
  var r = findRegistroByThreadId(body.thread_id);
  if (!r) return { success: false, error: "Sessão não encontrada" };

  var status = r.data[COL.STATUS];
  if (status !== "aberto") {
    if (status === "ativo")   return { success: false, error: "Ponto já está ativo" };
    if (status === "pausado") return { success: false, error: "Ponto está pausado — use Pausar/Retomar" };
    return { success: false, error: "Não é possível iniciar o ponto agora" };
  }

  var now   = new Date().toISOString();
  var sheet = getRegistros();
  sheet.getRange(r.row, COL.SESSION_INICIO      + 1).setValue(now);
  sheet.getRange(r.row, COL.SESSION_PAUSAS_JSON + 1).setValue("[]");
  sheet.getRange(r.row, COL.STATUS              + 1).setValue("ativo");

  return {
    success:        true,
    session_inicio: now,
    horas_semana:   Number(r.data[COL.HORAS_ACUMULADAS]),
    meta_horas:     Number(r.data[COL.META_HORAS]),
    week_start:     r.data[COL.WEEK_START],
    week_end:       r.data[COL.WEEK_END],
    status:         "ativo",
    user_id:        String(r.data[COL.USER_ID]),
  };
}

function pauseSession(body) {
  var r = findRegistroByThreadId(body.thread_id);
  if (!r)                              return { success: false, error: "Sessão não encontrada" };
  if (r.data[COL.STATUS] !== "ativo")  return { success: false, error: "Ponto não está ativo" };

  var now    = new Date().toISOString();
  var pausas = JSON.parse(r.data[COL.SESSION_PAUSAS_JSON] || "[]");
  pausas.push({ inicio: now, fim: null });

  var sessionH    = calcSessionHours(r.data[COL.SESSION_INICIO], null, JSON.stringify(pausas));
  var horasSemana = Number(r.data[COL.HORAS_ACUMULADAS]) + sessionH;

  var sheet = getRegistros();
  sheet.getRange(r.row, COL.STATUS              + 1).setValue("pausado");
  sheet.getRange(r.row, COL.SESSION_PAUSAS_JSON + 1).setValue(JSON.stringify(pausas));

  return {
    success:        true,
    session_inicio: r.data[COL.SESSION_INICIO],
    horas_semana:   horasSemana,
    meta_horas:     Number(r.data[COL.META_HORAS]),
    week_start:     r.data[COL.WEEK_START],
    week_end:       r.data[COL.WEEK_END],
    status:         "pausado",
    user_id:        String(r.data[COL.USER_ID]),
  };
}

function resumeSession(body) {
  var r = findRegistroByThreadId(body.thread_id);
  if (!r)                                return { success: false, error: "Sessão não encontrada" };
  if (r.data[COL.STATUS] !== "pausado")  return { success: false, error: "Ponto não está pausado" };

  var now    = new Date().toISOString();
  var pausas = JSON.parse(r.data[COL.SESSION_PAUSAS_JSON] || "[]");
  for (var i = pausas.length - 1; i >= 0; i--) {
    if (!pausas[i].fim) { pausas[i].fim = now; break; }
  }

  var sessionH    = calcSessionHours(r.data[COL.SESSION_INICIO], null, JSON.stringify(pausas));
  var horasSemana = Number(r.data[COL.HORAS_ACUMULADAS]) + sessionH;

  var sheet = getRegistros();
  sheet.getRange(r.row, COL.STATUS              + 1).setValue("ativo");
  sheet.getRange(r.row, COL.SESSION_PAUSAS_JSON + 1).setValue(JSON.stringify(pausas));

  return {
    success:        true,
    session_inicio: r.data[COL.SESSION_INICIO],
    horas_semana:   horasSemana,
    meta_horas:     Number(r.data[COL.META_HORAS]),
    week_start:     r.data[COL.WEEK_START],
    week_end:       r.data[COL.WEEK_END],
    status:         "ativo",
    user_id:        String(r.data[COL.USER_ID]),
  };
}

function closeSession(body) {
  var r = findRegistroByThreadId(body.thread_id);
  if (!r) return { success: false, error: "Sessão não encontrada" };

  var status = r.data[COL.STATUS];
  if (status !== "ativo" && status !== "pausado") {
    return { success: false, error: "Ponto não está ativo" };
  }

  var now    = new Date().toISOString();
  var pausas = JSON.parse(r.data[COL.SESSION_PAUSAS_JSON] || "[]");

  if (status === "pausado") {
    for (var i = pausas.length - 1; i >= 0; i--) {
      if (!pausas[i].fim) { pausas[i].fim = now; break; }
    }
  }

  var sessionH      = calcSessionHours(r.data[COL.SESSION_INICIO], now, JSON.stringify(pausas));
  var novaAcum      = Number(r.data[COL.HORAS_ACUMULADAS]) + sessionH;
  var meta          = Number(r.data[COL.META_HORAS]);
  var bounds        = getWeekBounds();
  var atingiu       = novaAcum >= meta;

  var newStatus;
  if (bounds.isSaturday && !atingiu) {
    newStatus = "incompleto";
  } else if (bounds.isSaturday && atingiu) {
    newStatus = "fechado";
  } else {
    newStatus = "aberto"; // semana continua, pronto para próxima sessão
  }

  var sheet = getRegistros();
  sheet.getRange(r.row, COL.HORAS_ACUMULADAS    + 1).setValue(novaAcum);
  sheet.getRange(r.row, COL.STATUS              + 1).setValue(newStatus);
  sheet.getRange(r.row, COL.SESSION_INICIO      + 1).setValue("");
  sheet.getRange(r.row, COL.SESSION_PAUSAS_JSON + 1).setValue("[]");

  return {
    success:      true,
    horas_semana: novaAcum,
    meta_horas:   meta,
    week_start:   r.data[COL.WEEK_START],
    week_end:     r.data[COL.WEEK_END],
    status:       newStatus,
    is_saturday:  bounds.isSaturday,
    user_id:      String(r.data[COL.USER_ID]),
  };
}

function justifySession(body) {
  var r = findRegistroByThreadId(body.thread_id);
  if (!r) return { success: false, error: "Sessão não encontrada" };
  if (r.data[COL.STATUS] !== "incompleto") {
    return { success: false, error: "Justificativa só é necessária em semanas incompletas" };
  }

  var sheet = getRegistros();
  sheet.getRange(r.row, COL.STATUS        + 1).setValue("justificado");
  sheet.getRange(r.row, COL.JUSTIFICATIVA + 1).setValue(body.justificativa);

  return {
    success:      true,
    horas_semana: Number(r.data[COL.HORAS_ACUMULADAS]),
    meta_horas:   Number(r.data[COL.META_HORAS]),
    week_start:   r.data[COL.WEEK_START],
    week_end:     r.data[COL.WEEK_END],
    status:       "justificado",
    user_id:      String(r.data[COL.USER_ID]),
  };
}

function getAllUsers(_body) {
  var usuariosData  = getUsuarios().getDataRange().getValues();
  var registrosData = getRegistros().getDataRange().getValues();
  var weekStart     = getWeekBounds().start;

  var users = [];
  for (var i = 1; i < usuariosData.length; i++) {
    var userId   = String(usuariosData[i][0]);
    var userName = usuariosData[i][1];
    var meta     = Number(usuariosData[i][2]);

    var weekRecord = null;
    for (var j = 1; j < registrosData.length; j++) {
      if (
        String(registrosData[j][COL.USER_ID])    === userId &&
        String(registrosData[j][COL.WEEK_START]) === weekStart
      ) {
        weekRecord = registrosData[j];
        break;
      }
    }

    var horasSemana = 0;
    var weekStatus  = "ausente";
    if (weekRecord) {
      var ws = weekRecord[COL.STATUS];
      var sh = (ws === "ativo" || ws === "pausado")
        ? calcSessionHours(weekRecord[COL.SESSION_INICIO], null, weekRecord[COL.SESSION_PAUSAS_JSON])
        : 0;
      horasSemana = Number(weekRecord[COL.HORAS_ACUMULADAS]) + sh;
      weekStatus  = ws;
    }

    users.push({
      user_id:      userId,
      user_name:    userName,
      meta_horas:   meta,
      today_status: weekStatus,
      today_horas:  horasSemana,
    });
  }

  return { success: true, users };
}

function setMeta(body) {
  var sheet = getUsuarios();
  var data  = sheet.getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (String(data[i][0]) === String(body.user_id)) {
      sheet.getRange(i + 1, 3).setValue(Number(body.meta_horas));
      return { success: true };
    }
  }
  return { success: false, error: "Usuário não encontrado. O usuário precisa ter clicado em Registro ao menos uma vez." };
}

function getRecords(body) {
  var limit = body.limit || 30;
  var data  = getRegistros().getDataRange().getValues();
  var records = [];

  for (var i = data.length - 1; i >= 1 && records.length < limit; i--) {
    if (String(data[i][COL.USER_ID]) !== String(body.user_id)) continue;
    records.push({
      thread_id:    String(data[i][COL.THREAD_ID]),
      week_start:   data[i][COL.WEEK_START],
      week_end:     data[i][COL.WEEK_END],
      horas_semana: Number(data[i][COL.HORAS_ACUMULADAS]),
      meta_horas:   Number(data[i][COL.META_HORAS]),
      status:       data[i][COL.STATUS],
      justificativa: data[i][COL.JUSTIFICATIVA] || null,
    });
  }

  return { success: true, records };
}

// ── Setup único (rodar manualmente no IDE do Apps Script) ─────────────────────

function setupSheets() {
  getRegistros();
  getUsuarios();
  var config = getOrCreateSheet("Config", ["chave","valor"]);
  if (config.getLastRow() <= 1) {
    config.appendRow(["default_meta", 5]);
  }
  SpreadsheetApp.getUi().alert("Planilhas criadas com sucesso!");
}
