(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module && module.exports) module.exports = api;
  if (root) root.BeliyConfig = api;
})(typeof window !== "undefined" ? window : null, function () {
  "use strict";

  var MARK_START = "===BELIY-OBHODCHIK-VLESS===";
  var MARK_END = "===BELIY-OBHODCHIK-VLESS-END===";

  function nowUtc() {
    return new Date().toISOString().slice(0, 19).replace("T", " ");
  }

  function parseSetupBlock(text) {
    var block = String(text || "");
    var start = block.indexOf(MARK_START);
    var end = block.indexOf(MARK_END);
    if (start === -1 || end === -1 || end < start) return null;
    var body = block.slice(start + MARK_START.length, end);
    var out = {};
    var keys = ["SERVER_IP", "UUID", "PUBLIC_KEY", "SHORT_ID", "SNI_HOSTNAME", "SNI_IP"];
    for (var i = 0; i < keys.length; i++) {
      var m = body.match(new RegExp("^" + keys[i] + "=(.*)$", "m"));
      if (m) out[keys[i].toLowerCase()] = m[1].trim();
    }
    if (!out.server_ip || !out.uuid || !out.public_key || !out.short_id || !out.sni_hostname) {
      return null;
    }
    return out;
  }

  function xrayConfig(p) {
    return {
      log: { loglevel: "warning" },
      dns: {
        servers: [
          "1.1.1.1",
          "1.0.0.1",
          {
            address: "8.8.8.8",
            port: 53,
            domains: ["geosite:geolocation-!cn"],
          },
        ],
      },
      inbounds: [
        {
          port: 10808,
          protocol: "socks",
          sniffing: { enabled: true, destOverride: ["http", "tls"] },
          settings: { auth: "noauth", udp: true },
        },
        { port: 10809, protocol: "http", settings: { timeout: 0 } },
      ],
      outbounds: [
        {
          protocol: "vless",
          settings: {
            vnext: [
              {
                address: p.server_ip,
                port: 443,
                users: [
                  {
                    id: p.uuid,
                    encryption: "none",
                    flow: "xtls-rprx-vision",
                  },
                ],
              },
            ],
          },
          streamSettings: {
            network: "tcp",
            security: "reality",
            realitySettings: {
              serverName: p.sni_hostname,
              fingerprint: "chrome",
              publicKey: p.public_key,
              shortId: p.short_id,
            },
          },
          tag: "proxy",
        },
        { protocol: "freedom", tag: "direct" },
        { protocol: "blackhole", tag: "block" },
      ],
      routing: {
        domainStrategy: "IPIfNonMatch",
        rules: [
          {
            type: "field",
            outboundTag: "proxy",
            domain: ["geosite:category-all"],
          },
          { type: "field", outboundTag: "direct", ip: ["geoip:private"] },
        ],
      },
    };
  }

  function singboxConfig(p) {
    return {
      log: { level: "warn" },
      dns: {
        servers: [
          { tag: "google", address: "8.8.8.8", detour: "direct" },
          { tag: "local", address: "223.5.5.5", detour: "direct" },
        ],
        rules: [{ outbound: "any", server: "local" }],
      },
      inbounds: [
        {
          type: "tun",
          tag: "tun-in",
          inet4_address: "172.19.0.1/30",
          auto_route: true,
          strict_route: true,
          stack: "system",
          sniff: true,
        },
      ],
      outbounds: [
        {
          type: "vless",
          tag: "proxy",
          server: p.server_ip,
          server_port: 443,
          uuid: p.uuid,
          flow: "xtls-rprx-vision",
          tls: {
            enabled: true,
            server_name: p.sni_hostname,
            utls: { enabled: true, fingerprint: "chrome" },
            reality: {
              enabled: true,
              public_key: p.public_key,
              short_id: p.short_id,
            },
          },
        },
        { type: "direct", tag: "direct" },
        { type: "block", tag: "block" },
        { type: "dns", tag: "dns-out" },
      ],
      route: {
        rules: [
          { protocol: "dns", outbound: "dns-out" },
          { geoip: ["private"], outbound: "direct" },
          { geosite: ["category-ads-all"], outbound: "block" },
        ],
        final: "proxy",
        auto_detect_interface: true,
      },
    };
  }

  var BLOCKED_DOMAINS = [
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "tiktok.com",
    "telegram.org",
    "t.me",
    "discord.com",
    "discordapp.com",
    "youtube.com",
    "youtu.be",
    "ytimg.com",
    "yt3.ggpht.com",
    "bbc.com",
    "cnn.com",
    "dw.com",
    "rferl.org",
    "linkedin.com",
    "reddit.com",
    "medium.com",
    "github.com",
    "gitlab.com",
  ];

  function hydrarouteRules(p, ts) {
    var out = "# HydraRoute правила для Keenetic\n";
    out += "# Сгенерировано: " + ts + "\n";
    out += "# SNI донор: " + p.sni_hostname + "\n";
    out += "# \n";
    out += "# Инструкция:\n";
    out += "# 1. Загрузите этот файл в HydraRoute\n";
    out += "# 2. Выберите VPN-туннель для этих доменов\n";
    out += "# 3. Остальной трафик пойдёт напрямую\n";
    out += "\n# === Социальные сети ===\n";
    for (var i = 0; i < BLOCKED_DOMAINS.length; i++) out += BLOCKED_DOMAINS[i] + "\n";
    out += "\n# === Конец списка ===\n";
    out += "# Добавьте свои домены по необходимости\n";
    return out;
  }

  function keeneticCli(p, ts) {
    var out = "! Автоматическая конфигурация Keenetic\n";
    out += "! Сгенерировано: " + ts + "\n";
    out += "! SNI донор: " + p.sni_hostname + "\n";
    out += "\n";
    out += "! Настройка Xray/Sing-box клиента\n";
    out += "! Выполните через CLI (SSH или Telnet):\n";
    out += "\n";
    out += "! 1. Создаём профиль Xray\n";
    out += "xray profile add name \"VLESS-Reality\"\n";
    out += "\n";
    out += "! 2. Настраиваем профиль\n";
    out += "xray profile VLESS-Reality\n";
    out += " protocol vless\n";
    out += " server " + p.server_ip + "\n";
    out += " port 443\n";
    out += " uuid " + p.uuid + "\n";
    out += " flow xtls-rprx-vision\n";
    out += " sni " + p.sni_hostname + "\n";
    out += " fingerprint chrome\n";
    out += " public-key " + p.public_key + "\n";
    out += " short-id " + p.short_id + "\n";
    out += " exit\n";
    out += "\n";
    out += "! 3. Создаём интерфейс\n";
    out += "interface Xray0\n";
    out += " description \"VLESS Reality VPN\"\n";
    out += " security-level private\n";
    out += " ip address 192.168.200.1 255.255.255.252\n";
    out += " up\n";
    out += "\n";
    out += "! 4. Настраиваем маршрут\n";
    out += "ip route default " + p.server_ip + " Xray0\n";
    out += "\n";
    out += "! 5. Сохраняем конфигурацию\n";
    out += "system configuration save\n";
    out += "\n";
    out += "! Готово! Проверьте подключение командой:\n";
    out += "! show interface Xray0\n";
    out += "\n";
    out += "! Для HydraRoute настройки используйте отдельный файл правил\n";
    return out;
  }

  function vlessUrl(p) {
    var params =
      "type=tcp&security=reality&pbk=" + p.public_key + "&fp=chrome&sni=" +
      p.sni_hostname + "&sid=" + p.short_id + "&flow=xtls-rprx-vision";
    return "vless://" + p.uuid + "@" + p.server_ip + ":443?" + params +
      "#VLESS-Reality-" + p.sni_hostname;
  }

  function readme(p, ts) {
    var out = "\n";
    out += "╔════════════════════════════════════════════════════════════╗\n";
    out += "║     АВТОМАТИЧЕСКАЯ НАСТРОЙКА KEENETIC                     ║\n";
    out += "║     VLESS Reality + HydraRoute                            ║\n";
    out += "╚════════════════════════════════════════════════════════════╝\n";
    out += "\n";
    out += "ДАННЫЕ ДЛЯ ПОДКЛЮЧЕНИЯ:\n";
    out += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n";
    out += "• Сервер: " + p.server_ip + ":443\n";
    out += "• UUID: " + p.uuid + "\n";
    out += "• SNI (маскировка): " + p.sni_hostname + "\n";
    out += "• Public Key: " + p.public_key + "\n";
    out += "• Short ID: " + p.short_id + "\n";
    out += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n";
    out += "\n";
    out += "СОДЕРЖИМОЕ АРХИВА:\n";
    out += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n";
    out += "1. xray_client.json      - Конфигурация для Xray\n";
    out += "2. singbox_client.json   - Конфигурация для Sing-box\n";
    out += "3. hydraroute_rules.txt  - Правила HydraRoute\n";
    out += "4. keenetic_cli.txt      - Команды для CLI\n";
    out += "5. vless_url.txt         - URL для импорта\n";
    out += "6. README.txt            - Эта инструкция\n";
    out += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n";
    out += "\n";
    out += "ВАРИАНТЫ УСТАНОВКИ:\n";
    out += "\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "ВАРИАНТ 1: Через Xray (рекомендуется)\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "\n";
    out += "1. Установите Xray на Keenetic:\n";
    out += "   • Веб-интерфейс → Приложения → Xray\n";
    out += "   • Или через OPKG: opkg install xray\n";
    out += "\n";
    out += "2. Загрузите конфигурацию:\n";
    out += "   • xray_client.json → загрузите в роутер\n";
    out += "   • Или скопируйте содержимое в веб-интерфейс\n";
    out += "\n";
    out += "3. Включите подключение в интерфейсе\n";
    out += "\n";
    out += "4. Настройте HydraRoute (см. ниже)\n";
    out += "\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "ВАРИАНТ 2: Через Sing-box\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "\n";
    out += "1. Установите Sing-box на Keenetic\n";
    out += "2. Загрузите singbox_client.json\n";
    out += "3. Включите подключение\n";
    out += "4. Настройте маршрутизацию\n";
    out += "\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "ВАРИАНТ 3: Через CLI (для продвинутых)\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "\n";
    out += "1. Подключитесь к роутеру по SSH или Telnet\n";
    out += "2. Выполните команды из файла keenetic_cli.txt\n";
    out += "3. Сохраните конфигурацию\n";
    out += "\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "НАСТРОЙКА HYDRAROUTE\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "\n";
    out += "HydraRoute позволяет пускать через VPN только заблокированные сайты,\n";
    out += "а остальной трафик отправлять напрямую.\n";
    out += "\n";
    out += "УСТАНОВКА:\n";
    out += "1. Установите HydraRoute на Keenetic\n";
    out += "2. Загрузите файл hydraroute_rules.txt\n";
    out += "3. Выберите VPN-туннель (VLESS Reality)\n";
    out += "4. Примените правила\n";
    out += "\n";
    out += "ПРИНЦИП РАБОТЫ:\n";
    out += "• DNS запросы перехватываются\n";
    out += "• Если домен в списке → IP добавляется в ipset\n";
    out += "• Трафик на эти IP идёт через VPN\n";
    out += "• Остальной трафик идёт напрямую\n";
    out += "\n";
    out += "ВАЖНО: \n";
    out += "• Устройства должны быть в политике \"по умолчанию\"\n";
    out += "• В политике HydraRoute только VPN-подключения\n";
    out += "\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "ПРОВЕРКА РАБОТЫ\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "\n";
    out += "1. Проверьте статус Xray/Sing-box\n";
    out += "2. Откройте заблокированный сайт (например, youtube.com)\n";
    out += "3. Проверьте IP на сайте 2ip.ru:\n";
    out += "   • Должен быть IP вашего VPS\n";
    out += "4. Проверьте скорость: speedtest.net\n";
    out += "\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "АВТОМАТИЧЕСКИЕ ОБНОВЛЕНИЯ\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "\n";
    out += "Система автоматически обновит SNI-донора при необходимости.\n";
    out += "\n";
    out += "ПРИНЦИП:\n";
    out += "• Мониторинг доступности донора\n";
    out += "• Автоматическая смена при блокировке\n";
    out += "• Обновление конфигурации в роутере\n";
    out += "\n";
    out += "Для проверки обновлений используйте скрипт update_check.sh\n";
    out += "\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "РЕШЕНИЕ ПРОБЛЕМ\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "\n";
    out += "ПРОБЛЕМА: Сайты не открываются\n";
    out += "РЕШЕНИЕ: \n";
    out += "• Проверьте статус Xray/Sing-box\n";
    out += "• Проверьте правильность UUID и ключей\n";
    out += "• Проверьте доступность сервера: ping " + p.server_ip + "\n";
    out += "\n";
    out += "ПРОБЛЕМА: Медленная скорость\n";
    out += "РЕШЕНИЕ:\n";
    out += "• Выберите VPS ближе к вам географически\n";
    out += "• Проверьте загрузку сервера\n";
    out += "• Попробуйте другой SNI-донор\n";
    out += "\n";
    out += "ПРОБЛЕМА: Блокировка оператором\n";
    out += "РЕШЕНИЕ:\n";
    out += "• Система автоматически сменит донора\n";
    out += "• Или запросите обновление вручную\n";
    out += "\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "ПОДДЕРЖКА\n";
    out += "═══════════════════════════════════════════════════════════\n";
    out += "\n";
    out += "Telegram: @beliy_obhodchik_support_bot\n";
    out += "FAQ: /faq в боте @beliy_obhodchik_bot\n";
    out += "\n";
    out += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n";
    out += "Сгенерировано автоматически: " + ts + "\n";
    out += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n";
    return out;
  }

  function buildFiles(p, ts) {
    ts = ts || nowUtc();
    var files = {};
    files["xray_client.json"] = JSON.stringify(xrayConfig(p), null, 2);
    files["singbox_client.json"] = JSON.stringify(singboxConfig(p), null, 2);
    files["hydraroute_rules.txt"] = hydrarouteRules(p, ts);
    files["keenetic_cli.txt"] = keeneticCli(p, ts);
    files["vless_url.txt"] = vlessUrl(p);
    files["README.txt"] = readme(p, ts);
    return files;
  }

  var CRC_TABLE = (function () {
    var table = new Uint32Array(256);
    for (var n = 0; n < 256; n++) {
      var c = n;
      for (var k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      table[n] = c >>> 0;
    }
    return table;
  })();

  function crc32(bytes) {
    var c = 0xffffffff;
    for (var i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }

  var DOS_TIME = 0;
  var DOS_DATE = ((2026 - 1980) << 9) | (9 << 5) | 29;

  function makeZip(files) {
    var enc = typeof TextEncoder !== "undefined" ? new TextEncoder() : null;
    var utf8 = function (s) {
      if (enc) return enc.encode(s);
      var arr = [];
      for (var i = 0; i < s.length; i++) {
        var c = s.charCodeAt(i);
        if (c < 128) arr.push(c);
        else if (c < 2048) arr.push(192 | (c >> 6), 128 | (c & 63));
        else arr.push(224 | (c >> 12), 128 | ((c >> 6) & 63), 128 | (c & 63));
      }
      return new Uint8Array(arr);
    };

    var names = Object.keys(files);
    var locals = [];
    var centrals = [];
    var offset = 0;

    for (var i = 0; i < names.length; i++) {
      var name = utf8(names[i]);
      var data = utf8(String(files[names[i]]));
      var crc = crc32(data);

      var local = new Uint8Array(30 + name.length + data.length);
      var lv = new DataView(local.buffer);
      lv.setUint32(0, 0x04034b50, true);
      lv.setUint16(4, 20, true);
      lv.setUint16(6, 0, true);
      lv.setUint16(8, 0, true);
      lv.setUint16(10, DOS_TIME, true);
      lv.setUint16(12, DOS_DATE, true);
      lv.setUint32(14, crc, true);
      lv.setUint32(18, data.length, true);
      lv.setUint32(22, data.length, true);
      lv.setUint16(26, name.length, true);
      lv.setUint16(28, 0, true);
      local.set(name, 30);
      local.set(data, 30 + name.length);
      locals.push(local);

      var central = new Uint8Array(46 + name.length);
      var cv = new DataView(central.buffer);
      cv.setUint32(0, 0x02014b50, true);
      cv.setUint16(4, 20, true);
      cv.setUint16(6, 20, true);
      cv.setUint16(8, 0, true);
      cv.setUint16(10, 0, true);
      cv.setUint16(12, DOS_TIME, true);
      cv.setUint16(14, DOS_DATE, true);
      cv.setUint32(16, crc, true);
      cv.setUint32(20, data.length, true);
      cv.setUint32(24, data.length, true);
      cv.setUint16(28, name.length, true);
      cv.setUint16(30, 0, true);
      cv.setUint16(32, 0, true);
      cv.setUint16(34, 0, true);
      cv.setUint16(36, 0, true);
      cv.setUint32(38, 0, true);
      cv.setUint32(42, offset, true);
      central.set(name, 46);
      centrals.push(central);

      offset += local.length;
    }

    var centralSize = 0;
    for (var j = 0; j < centrals.length; j++) centralSize += centrals[j].length;

    var eocd = new Uint8Array(22);
    var ev = new DataView(eocd.buffer);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(4, 0, true);
    ev.setUint16(6, 0, true);
    ev.setUint16(8, names.length, true);
    ev.setUint16(10, names.length, true);
    ev.setUint32(12, centralSize, true);
    ev.setUint32(16, offset, true);
    ev.setUint16(20, 0, true);

    var total = offset + centralSize + eocd.length;
    var out = new Uint8Array(total);
    var pos = 0;
    for (var a = 0; a < locals.length; a++) {
      out.set(locals[a], pos);
      pos += locals[a].length;
    }
    for (var b = 0; b < centrals.length; b++) {
      out.set(centrals[b], pos);
      pos += centrals[b].length;
    }
    out.set(eocd, pos);
    return out;
  }

  var api = {
    MARK_START: MARK_START,
    MARK_END: MARK_END,
    parseSetupBlock: parseSetupBlock,
    buildFiles: buildFiles,
    makeZip: makeZip,
    crc32: crc32,
  };

  if (typeof require === "function" && typeof module === "object" && module && module.exports) {
    var fs = null;
    try {
      fs = require("fs");
    } catch (e) {
      fs = null;
    }
    if (fs && typeof process !== "undefined" && process.argv && process.argv[2] === "--zip") {
      var params = JSON.parse(process.argv[3]);
      var bytes = makeZip(buildFiles(params));
      fs.writeFileSync(process.argv[4], bytes);
    }
  }

  return api;
});
