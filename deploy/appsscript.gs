/**
 * appsscript.gs — เก็บราคาจริง (bid/ask) ของตลาด "อุณหภูมิสูงสุด" ลง Google Sheet ฟรี
 * ใช้เมื่อ: ไม่มี GitHub / ไม่อยากแตะ terminal / ไม่อยากผูกบัตร
 *
 * วิธีใช้:
 *  1) script.google.com → New project → วางโค้ดนี้
 *  2) ตั้ง Script property: SHEET_ID (ถ้าไม่ตั้ง จะสร้างชีตใหม่ให้แล้วจำ id ไว้)
 *  3) Triggers → Add Trigger → logQuotes → Time-driven → Hour timer
 *  4) รันครั้งแรกจะสร้างชีตชื่อ "wxedge_quotes"
 *
 * หมายเหตุ: Apps Script เป็น JavaScript จึงไม่รัน cheap_live.py ตรง ๆ ได้
 *           โค้ดนี้เก็บ "ราคาจริง" (bid/ask/last) ของ bin ที่ราคาอยู่ในช่วงน่าสนใจ
 *           แล้วค่อย export CSV ไปวิเคราะห์ด้วยชุดเครื่องมือ Python ในโปรเจกต์
 */

var CITIES = [
  'nyc', 'chicago', 'miami', 'dallas', 'austin', 'denver', 'seattle', 'atlanta',
  'los-angeles', 'houston', 'toronto', 'mexico-city', 'sao-paulo', 'buenos-aires',
  'london', 'paris', 'madrid', 'milan', 'munich', 'amsterdam', 'warsaw', 'helsinki',
  'moscow', 'istanbul', 'ankara', 'tokyo', 'seoul', 'beijing', 'shanghai', 'hong-kong',
  'singapore', 'kuala-lumpur', 'manila', 'taipei', 'sydney', 'wellington', 'cape-town',
  'jeddah', 'tel-aviv', 'lucknow', 'karachi', 'chengdu', 'chongqing', 'wuhan', 'busan',
  'panama-city', 'jinan', 'guangzhou', 'shenzhen', 'qingdao'
];

var MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july',
  'august', 'september', 'october', 'november', 'december'];

function logQuotes() {
  var sheet = getSheet_();
  var now = new Date();
  var rows = [];
  var day = Utilities.formatDate(now, 'UTC', 'yyyy-MM-dd');

  for (var i = 0; i < CITIES.length; i++) {
    var slug = 'highest-temperature-in-' + CITIES[i] + '-on-' +
      MONTHS[now.getUTCMonth()] + '-' + now.getUTCDate() + '-' + now.getUTCFullYear();
    var ev = gamma_('https://gamma-api.polymarket.com/events?slug=' + slug);
    if (!ev || !ev.length || ev[0].closed) continue;
    var markets = ev[0].markets || [];
    for (var j = 0; j < markets.length; j++) {
      var m = markets[j];
      var ask = parseFloat(m.bestAsk);
      var bid = parseFloat(m.bestBid);
      if (isNaN(ask)) continue;
      if (ask >= 0.99 || ask <= 0.02) continue;          // ปลายสมุด/ตัดสินแล้ว ไม่มีประโยชน์
      var lt = lastTrade_(m.clobTokenIds, day);
      rows.push([new Date(), CITIES[i], day, m.groupItemTitle, bid, ask, lt,
        parseFloat(m.volumeNum || 0), (isNaN(bid) ? '' : (ask - bid).toFixed(4))]);
      Utilities.sleep(150);                               // ถนอม rate limit
    }
  }
  if (rows.length) {
    sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
  }
  Logger.log('เก็บ %s แถว', rows.length);
}

function lastTrade_(clobTokenIds, day) {
  try {
    var toks = JSON.parse(clobTokenIds || '[]');
    if (!toks.length) return '';
    var url = 'https://clob.polymarket.com/prices-history?market=' + toks[0] + '&interval=max&fidelity=60';
    var j = gamma_(url);
    if (!j || !j.history || !j.history.length) return '';
    return j.history[j.history.length - 1].p;
  } catch (e) {
    return '';
  }
}

function gamma_(url) {
  try {
    var res = UrlFetchApp.fetch(url, {
      muteHttpExceptions: true,
      headers: { 'User-Agent': 'wxedge-gas/1.0' }
    });
    if (res.getResponseCode() !== 200) return null;
    return JSON.parse(res.getContentText());
  } catch (e) {
    return null;
  }
}

function getSheet_() {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty('SHEET_ID');
  var ss;
  if (id) {
    ss = SpreadsheetApp.openById(id);
  } else {
    ss = SpreadsheetApp.create('wxedge_quotes');
    props.setProperty('SHEET_ID', ss.getId());
  }
  var sh = ss.getSheetByName('quotes') || ss.insertSheet('quotes');
  if (sh.getLastRow() === 0) {
    sh.appendRow(['ts_utc', 'city', 'date', 'bin', 'yes_bid', 'yes_ask', 'last_trade', 'volume', 'spread']);
  }
  return sh;
}
