(function () {
  'use strict';

  var TC = {};
  window.TC = TC;

  /* ======================= util ======================= */

  var U = TC.util = {
    esc: function (s) {
      s = (s == null) ? '' : String(s);
      return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },
    round1: function (n) { return Math.round(n * 10) / 10; },
    haversineMi: function (lat1, lon1, lat2, lon2) {
      var R = 3958.8, toRad = Math.PI / 180;
      var phi1 = lat1 * toRad, phi2 = lat2 * toRad;
      var dphi = (lat2 - lat1) * toRad, dl = (lon2 - lon1) * toRad;
      var a = Math.sin(dphi / 2) * Math.sin(dphi / 2) +
        Math.cos(phi1) * Math.cos(phi2) * Math.sin(dl / 2) * Math.sin(dl / 2);
      return 2 * R * Math.asin(Math.sqrt(a));
    },
    parseAgeYears: function (s) {
      if (!s) return null;
      var m = /^(\d+)\s*Year/i.exec(String(s).trim());
      return m ? parseInt(m[1], 10) : null;
    },
    el: function (tag, attrs, html) {
      var e = document.createElement(tag);
      if (attrs) for (var k in attrs) {
        if (k === 'class') e.className = attrs[k];
        else if (k === 'html') e.innerHTML = attrs[k];
        else e.setAttribute(k, attrs[k]);
      }
      if (html != null) e.innerHTML = html;
      return e;
    },
    qs: function (sel, root) { return (root || document).querySelector(sel); },
    on: function (el, ev, fn) { el.addEventListener(ev, fn); }
  };

  /* ======================= CSV parsing (fetch mode) ======================= */

  function parseCSV(text) {
    var rows = [];
    var i = 0, n = text.length, field = '', row = [], inQuotes = false;
    while (i < n) {
      var c = text[i];
      if (inQuotes) {
        if (c === '"') {
          if (text[i + 1] === '"') { field += '"'; i += 2; continue; }
          inQuotes = false; i++; continue;
        }
        field += c; i++; continue;
      } else {
        if (c === '"') { inQuotes = true; i++; continue; }
        if (c === ',') { row.push(field); field = ''; i++; continue; }
        if (c === '\r') { i++; continue; }
        if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; i++; continue; }
        field += c; i++; continue;
      }
    }
    if (field.length || row.length) { row.push(field); rows.push(row); }
    if (!rows.length) return [];
    var header = rows[0];
    var out = [];
    for (var r = 1; r < rows.length; r++) {
      if (rows[r].length === 1 && rows[r][0] === '') continue;
      var obj = {};
      for (var c2 = 0; c2 < header.length; c2++) obj[header[c2]] = rows[r][c2] !== undefined ? rows[r][c2] : '';
      out.push(obj);
    }
    return out;
  }

  function expandCompact(compact) {
    var fields = compact.fields, rows = compact.rows, out = new Array(rows.length);
    for (var i = 0; i < rows.length; i++) {
      var obj = {}, r = rows[i];
      for (var j = 0; j < fields.length; j++) obj[fields[j]] = r[j];
      out[i] = obj;
    }
    return out;
  }

  /* ======================= normalization ======================= */

  var NUM = {
    studies: ['enrollment_count', 'us_site_count'],
    sites: ['lat', 'lon', 'x', 'y'],
    patients: ['age_years', 'prior_lines', 'ecog', 'home_lat', 'home_lon', 'x', 'y',
      'ring_25_r', 'ring_50_r', 'ring_100_r', 'travel_radius_mi']
  };

  function toNum(v) {
    if (v === '' || v == null) return null;
    var n = parseFloat(v);
    return isNaN(n) ? null : n;
  }

  function normalizeRows(rows, numFields) {
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      for (var j = 0; j < numFields.length; j++) r[numFields[j]] = toNum(r[numFields[j]]);
    }
    return rows;
  }

  function normalizeStudy(s) {
    s.phases = (s.phases || '').split('|').filter(Boolean);
    s.conditions = (s.conditions || '').split('|').filter(Boolean);
    s.tumor_types_matched = (s.tumor_types_matched || '').split('|').filter(Boolean);
    return s;
  }

  /* ======================= data layer ======================= */

  TC.data = {
    studies: [], sites: [], signals: [], patients: [],
    meta: {}, gap: {}, landscape: {}, shortlists: {}, states: null,
    sitesByNct: {}, signalsByNct: {}, studyByNct: {},

    load: function () {
      var mode = window.__TC_MODE__;
      if (mode === 'fetch') return this._loadFetch();
      return this._loadEmbedded();
    },

    _loadEmbedded: function () {
      var self = this;
      return Promise.resolve().then(function () {
        var d = window.__TC_EMBEDDED__;
        self._ingest({
          studies: expandCompact(d.studies),
          sites: expandCompact(d.sites),
          signals: expandCompact(d.signals),
          patients: expandCompact(d.patients),
          extras: { meta: d.meta, gap: d.gap, landscape: d.landscape, shortlists: d.shortlists, states: d.states }
        });
      });
    },

    _loadFetch: function () {
      var self = this;
      var base = 'data/';
      return Promise.all([
        fetch(base + 'studies.csv').then(function (r) { return r.text(); }),
        fetch(base + 'sites.csv').then(function (r) { return r.text(); }),
        fetch(base + 'criteria_signals.csv').then(function (r) { return r.text(); }),
        fetch(base + 'patients.csv').then(function (r) { return r.text(); }),
        fetch(window.__TC_DATA_URL__).then(function (r) { return r.json(); })
      ]).then(function (res) {
        self._ingest({
          studies: parseCSV(res[0]),
          sites: parseCSV(res[1]),
          signals: parseCSV(res[2]),
          patients: parseCSV(res[3]),
          extras: res[4]
        });
      });
    },

    _ingest: function (d) {
      this.studies = normalizeRows(d.studies, NUM.studies).map(normalizeStudy);
      this.sites = normalizeRows(d.sites, NUM.sites);
      this.signals = d.signals;
      this.patients = normalizeRows(d.patients, NUM.patients);
      this.meta = d.extras.meta;
      this.gap = d.extras.gap;
      this.landscape = d.extras.landscape;
      this.shortlists = d.extras.shortlists;
      this.states = d.extras.states;

      var sitesByNct = {}, i;
      for (i = 0; i < this.sites.length; i++) {
        var s = this.sites[i];
        (sitesByNct[s.nct_id] = sitesByNct[s.nct_id] || []).push(s);
      }
      this.sitesByNct = sitesByNct;

      var signalsByNct = {};
      for (i = 0; i < this.signals.length; i++) {
        var sig = this.signals[i];
        (signalsByNct[sig.nct_id] = signalsByNct[sig.nct_id] || []).push(sig);
      }
      this.signalsByNct = signalsByNct;

      var studyByNct = {};
      for (i = 0; i < this.studies.length; i++) studyByNct[this.studies[i].nct_id] = this.studies[i];
      this.studyByNct = studyByNct;
    }
  };

  /* ======================= matching engine (mirrors matching.py) ======================= */

  var BIOMARKERS = ['HER2', 'EGFR', 'KRAS', 'BRCA1', 'BRCA2', 'BRCA', 'PD-L1', 'PDL1',
    'ALK', 'ROS1', 'BRAF', 'MSI-H', 'MSI', 'MET', 'NTRK', 'PIK3CA', 'MMR'];

  function biomarkerTokenRe(token) {
    var esc = token.replace(/[.*+?^${}()|[\]\\-]/g, '\\$&');
    var flags = token === 'MET' ? '' : 'i';
    return new RegExp('\\b' + esc + '\\b', flags);
  }
  var BIOMARKER_RES = BIOMARKERS.map(function (b) { return { token: b, re: biomarkerTokenRe(b) }; });

  function patientBiomarkerTokens(patient) {
    var text = patient.biomarkers || '';
    var found = {};
    BIOMARKER_RES.forEach(function (b) { if (b.re.test(text)) found[b.token] = true; });
    return found;
  }

  function sexOk(studySex, patientSex) {
    var s = (studySex || 'ALL').toUpperCase();
    return s === 'ALL' || s === patientSex.toUpperCase();
  }

  function ageOk(study, patientAge) {
    var lo = U.parseAgeYears(study.min_age), hi = U.parseAgeYears(study.max_age);
    if (lo != null && patientAge < lo) return { ok: false, lo: lo, hi: hi };
    if (hi != null && patientAge > hi) return { ok: false, lo: lo, hi: hi };
    return { ok: true, lo: lo, hi: hi };
  }

  function nearestSite(patient, siteRows) {
    var best = null, bestD = null;
    for (var i = 0; i < siteRows.length; i++) {
      var s = siteRows[i];
      if (s.lat == null || s.lon == null) continue;
      var d = U.haversineMi(patient.home_lat, patient.home_lon, s.lat, s.lon);
      if (bestD == null || d < bestD) { bestD = d; best = s; }
    }
    if (!best) return null;
    var out = {};
    for (var k in best) out[k] = best[k];
    out.distance_mi = U.round1(bestD);
    return out;
  }

  function softSignalChips(patient, signals) {
    var chips = [];
    var pTokens = patientBiomarkerTokens(patient);
    var pEcog = (patient.ecog == null) ? null : parseInt(patient.ecog, 10);
    var pLines = (patient.prior_lines == null) ? null : parseInt(patient.prior_lines, 10);

    signals.forEach(function (sig) {
      if (sig.signal_type === 'ecog') {
        var parts = sig.detail.split('-'), lo = parseInt(parts[0], 10), hi = parseInt(parts[1], 10);
        if (pEcog == null) return;
        var fits = pEcog >= lo && pEcog <= hi;
        chips.push({
          kind: 'soft', label: sig.label + ' · patient ECOG ' + pEcog,
          polarity: fits ? 'match' : 'caution', detail: sig.section, source_sentence: sig.source_sentence
        });
      } else if (sig.signal_type === 'biomarker') {
        var dp = sig.detail.split(':'), token = dp[0], qualifier = dp[1];
        if (!pTokens[token]) return;
        var excluding = sig.section === 'exclusion';
        chips.push({
          kind: 'soft',
          label: token + ' ' + (qualifier !== 'unspecified' ? qualifier : 'mentioned') + (excluding ? ' (exclusion criterion)' : ''),
          polarity: excluding ? 'caution' : 'match', detail: sig.section, source_sentence: sig.source_sentence
        });
      } else if (sig.signal_type === 'prior_therapy') {
        if (pLines == null) return;
        var fits2;
        if (sig.detail.indexOf('naive') === 0) {
          fits2 = pLines === 0;
        } else {
          var m = /^(>=|<=|==)(\d+)/.exec(sig.detail);
          if (!m) return;
          var n = parseInt(m[2], 10);
          if (m[1] === '>=') fits2 = pLines >= n;
          else if (m[1] === '<=') fits2 = pLines <= n;
          else fits2 = pLines === n;
        }
        chips.push({
          kind: 'soft', label: sig.label + ' · patient has ' + pLines,
          polarity: fits2 ? 'match' : 'caution', detail: sig.section, source_sentence: sig.source_sentence
        });
      }
    });
    return chips;
  }

  TC.match = {
    matchPatient: function (patient) {
      var studies = TC.data.studies, sitesByNct = TC.data.sitesByNct, signalsByNct = TC.data.signalsByNct;
      var matches = [], nearMisses = [];
      var pAge = patient.age_years, pSex = patient.sex, pRadius = patient.travel_radius_mi, pTumor = patient.tumor_type;

      for (var i = 0; i < studies.length; i++) {
        var study = studies[i];
        if (study.tumor_types_matched.indexOf(pTumor) === -1) continue;

        var siteRows = sitesByNct[study.nct_id] || [];
        var nearest = nearestSite(patient, siteRows);
        var distanceMi = nearest ? nearest.distance_mi : null;

        var sOk = sexOk(study.sex, pSex);
        var aRes = ageOk(study, pAge);
        var dOk = distanceMi != null && distanceMi <= pRadius;

        var failed = [];
        if (!sOk) failed.push({ kind: 'sex', reason: 'Trial enrolls ' + (study.sex || 'ALL').toLowerCase() + ' participants only; patient is ' + pSex + '.' });
        if (!aRes.ok) {
          if (aRes.lo != null && pAge < aRes.lo) failed.push({ kind: 'age', reason: 'Trial requires age ' + aRes.lo + '+ ; patient is ' + pAge + '.' });
          else if (aRes.hi != null && pAge > aRes.hi) failed.push({ kind: 'age', reason: 'Trial enrolls up to age ' + aRes.hi + '; patient is ' + pAge + '.' });
        }
        if (!dOk) {
          if (distanceMi == null) failed.push({ kind: 'distance', reason: 'No geocoded recruiting site found for this trial.' });
          else failed.push({ kind: 'distance', reason: 'Nearest site is ' + distanceMi + ' mi away, outside the ' + Math.round(pRadius) + ' mi travel radius.' });
        }

        if (failed.length === 0) {
          var hardChips = [
            { kind: 'hard', label: 'Age ' + pAge + ' within ' + (aRes.lo || 0) + '–' + (aRes.hi || '+'), polarity: 'match' },
            { kind: 'hard', label: 'Sex: ' + (study.sex || 'ALL'), polarity: 'match' },
            { kind: 'hard', label: distanceMi + ' mi · within ' + Math.round(pRadius) + ' mi radius', polarity: 'match' }
          ];
          var softChips = softSignalChips(patient, signalsByNct[study.nct_id] || []);
          matches.push({ nct_id: study.nct_id, distance_mi: distanceMi, hard_chips: hardChips, soft_chips: softChips, nearest_site: nearest });
        } else if (failed.length === 1) {
          nearMisses.push({ nct_id: study.nct_id, distance_mi: distanceMi, excluding_kind: failed[0].kind, excluding_reason: failed[0].reason });
        }
      }

      matches.sort(function (a, b) { return a.distance_mi - b.distance_mi || (a.nct_id < b.nct_id ? -1 : 1); });
      nearMisses.sort(function (a, b) {
        var da = a.distance_mi == null ? 1e9 : a.distance_mi, db = b.distance_mi == null ? 1e9 : b.distance_mi;
        return da - db || (a.nct_id < b.nct_id ? -1 : 1);
      });
      return { matches: matches, nearMisses: nearMisses };
    },
    nearestSite: nearestSite
  };

  /* ======================= router ======================= */

  TC.router = {
    parse: function () {
      var hash = location.hash.replace(/^#\/?/, '');
      var parts = hash.split('?');
      var segs = parts[0].split('/').filter(Boolean);
      var q = {};
      if (parts[1]) parts[1].split('&').forEach(function (kv) {
        var p = kv.split('=');
        q[decodeURIComponent(p[0])] = decodeURIComponent(p[1] || '');
      });
      return { view: segs[0] || 'gap', param: segs[1] || null, query: q };
    },
    navigate: function (view, param, query) {
      var h = '#/' + view + (param ? '/' + param : '');
      if (query && Object.keys(query).length) {
        h += '?' + Object.keys(query).map(function (k) { return k + '=' + encodeURIComponent(query[k]); }).join('&');
      }
      location.hash = h;
    }
  };

  /* ======================= shared render helpers ======================= */

  function chipEl(chip) {
    var cls = 'tc-chip ' + (chip.kind === 'hard' ? 'tc-chip-hard' : 'tc-chip-soft') + ' tc-chip-' + chip.polarity;
    var title = chip.source_sentence ? U.esc(chip.source_sentence) : '';
    return '<span class="' + cls + '"' + (title ? ' title="' + title + '"' : '') + '>' + U.esc(chip.label) + '</span>';
  }

  function chipsHtml(chips) {
    if (!chips || !chips.length) return '';
    return '<div class="tc-chips">' + chips.map(chipEl).join('') + '</div>';
  }

  function studyPhaseLabel(study) {
    return study.phases.length ? study.phases.map(function (p) { return p.replace('PHASE', 'Phase '); }).join(', ') : 'Not applicable';
  }

  function sponsorLabel(cls) {
    var map = { INDUSTRY: 'Industry', OTHER: 'Academic / Other', OTHER_GOV: 'Government', NIH: 'NIH', NETWORK: 'Network', FED: 'Federal' };
    return map[cls] || cls || 'Unknown';
  }

  function patientPickerHtml(activePatientId) {
    var html = '<div class="tc-patient-picker">';
    TC.data.patients.forEach(function (p) {
      var cls = 'tc-patient-pill' + (p.patient_id === activePatientId ? ' active' : '');
      html += '<button type="button" class="' + cls + '" data-patient="' + U.esc(p.patient_id) + '">' + U.esc(p.patient_label) + '</button>';
    });
    html += '</div>';
    return html;
  }

  function attrPanelHtml(patient) {
    return (
      '<details class="tc-attr-panel" id="tc-attr-panel">' +
      '<summary>Edit patient attributes</summary>' +
      '<div class="tc-attr-grid">' +
      field('Tumor type', 'select', 'tumor_type', patient.tumor_type, ['breast', 'nsclc', 'colorectal', 'prostate']) +
      field('Sex', 'select', 'sex', patient.sex, ['female', 'male']) +
      field('Age', 'number', 'age_years', patient.age_years) +
      field('ECOG', 'select', 'ecog', String(patient.ecog), ['0', '1', '2', '3', '4']) +
      field('Prior lines', 'number', 'prior_lines', patient.prior_lines) +
      field('Biomarkers', 'text', 'biomarkers', patient.biomarkers) +
      field('Travel radius (mi)', 'number', 'travel_radius_mi', patient.travel_radius_mi) +
      '</div></details>'
    );
  }

  function field(label, type, name, value, options) {
    var input;
    if (type === 'select') {
      input = '<select data-attr="' + name + '">' + options.map(function (o) {
        return '<option value="' + U.esc(o) + '"' + (String(o) === String(value) ? ' selected' : '') + '>' + U.esc(o) + '</option>';
      }).join('') + '</select>';
    } else {
      input = '<input type="' + type + '" data-attr="' + name + '" value="' + U.esc(value) + '">';
    }
    return '<div class="tc-field"><label>' + U.esc(label) + '</label>' + input + '</div>';
  }

  function readAttrPanel(base) {
    var patient = {};
    for (var k in base) patient[k] = base[k];
    var panel = U.qs('#tc-attr-panel');
    if (!panel) return patient;
    panel.querySelectorAll('[data-attr]').forEach(function (input) {
      var name = input.getAttribute('data-attr');
      var v = input.value;
      if (['age_years', 'prior_lines', 'ecog', 'travel_radius_mi'].indexOf(name) !== -1) v = parseFloat(v);
      patient[name] = v;
    });
    return patient;
  }

  /* ======================= view: The Gap ======================= */

  var TUMOR_LABELS = { breast: 'Breast', nsclc: 'NSCLC', colorectal: 'Colorectal', prostate: 'Prostate' };

  var STATE_ABBR = {
    Alabama: 'AL', Alaska: 'AK', Arizona: 'AZ', Arkansas: 'AR', California: 'CA', Colorado: 'CO',
    Connecticut: 'CT', Delaware: 'DE', 'District of Columbia': 'DC', Florida: 'FL', Georgia: 'GA',
    Hawaii: 'HI', Idaho: 'ID', Illinois: 'IL', Indiana: 'IN', Iowa: 'IA', Kansas: 'KS', Kentucky: 'KY',
    Louisiana: 'LA', Maine: 'ME', Maryland: 'MD', Massachusetts: 'MA', Michigan: 'MI', Minnesota: 'MN',
    Mississippi: 'MS', Missouri: 'MO', Montana: 'MT', Nebraska: 'NE', Nevada: 'NV', 'New Hampshire': 'NH',
    'New Jersey': 'NJ', 'New Mexico': 'NM', 'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND',
    Ohio: 'OH', Oklahoma: 'OK', Oregon: 'OR', Pennsylvania: 'PA', 'Rhode Island': 'RI',
    'South Carolina': 'SC', 'South Dakota': 'SD', Tennessee: 'TN', Texas: 'TX', Utah: 'UT',
    Vermont: 'VT', Virginia: 'VA', Washington: 'WA', 'West Virginia': 'WV', Wisconsin: 'WI', Wyoming: 'WY'
  };
  function stateAbbr(name) { return STATE_ABBR[name] || name || ''; }
  function cityState(site) { return (site.city || '') + ', ' + stateAbbr(site.state); }

  TC.views = {};
  TC.views.gap = {
    render: function (root) {
      var gap = TC.data.gap, meta = TC.data.meta;
      var byType = gap.active_studies_by_tumor_type;
      var maxCount = Math.max.apply(null, Object.keys(byType).map(function (k) { return byType[k]; }));

      var html = '';
      html += headHtml('The gap', 'What the network can actually offer',
        'Across the eight demo profiles, this is how many recruiting studies are within reach, and how thin the options get by tumor type.');

      html += '<div class="tc-grid tc-grid-4 tc-mt">';
      html += statCard(gap.matched_study_count, 'Studies matching ≥ 1 demo profile');
      html += statCard(meta.active_study_count, 'Recruiting studies with a US site');
      html += statCard((gap.median_nearest_site_mi != null ? gap.median_nearest_site_mi : '–') + ' mi', 'Median distance to nearest relevant site');
      html += statCard(meta.snapshot_date, 'Snapshot date');
      html += '</div>';

      html += '<div class="tc-section-label">Active studies by tumor type</div>';
      html += '<div class="tc-card">';
      Object.keys(TUMOR_LABELS).forEach(function (t) {
        var count = byType[t] || 0;
        var pct = maxCount ? (count / maxCount * 100) : 0;
        html += '<div class="tc-bar-row">' +
          '<div class="tc-bar-label">' + TUMOR_LABELS[t] + '</div>' +
          '<div class="tc-bar-track"><div class="tc-bar-fill" style="width:' + pct + '%"></div></div>' +
          '<div class="tc-bar-value">' + count + '</div>' +
          '</div>';
      });
      html += '</div>';

      html += '<div class="tc-section-label">Per demo profile</div>';
      html += '<div class="tc-grid tc-grid-2">';
      gap.per_patient.forEach(function (pp) {
        var patient = TC.data.patients.filter(function (p) { return p.patient_id === pp.patient_id; })[0];
        html += '<div class="tc-card">' +
          '<div class="upper tc-muted" style="font-size:11px;margin-bottom:6px;">' + U.esc(patient.patient_label) + ' · ' + TUMOR_LABELS[patient.tumor_type] + '</div>' +
          '<div style="display:flex;gap:20px;">' +
          '<div><div class="tc-stat-value" style="font-size:26px;">' + pp.match_count + '</div><div class="tc-stat-label">Matches</div></div>' +
          '<div><div class="tc-stat-value" style="font-size:26px;color:var(--dim);">' + pp.near_miss_count + '</div><div class="tc-stat-label">Near-misses</div></div>' +
          '<div><div class="tc-stat-value" style="font-size:26px;color:var(--turquoise);">' + (pp.nearest_relevant_site_mi != null ? pp.nearest_relevant_site_mi : '–') + '</div><div class="tc-stat-label">Nearest site (mi)</div></div>' +
          '</div>' +
          '<a href="#/match/' + patient.patient_id + '" class="tc-btn tc-mt" style="display:inline-block;">View shortlist</a>' +
          '</div>';
      });
      html += '</div>';

      root.innerHTML = html;
    }
  };

  function statCard(value, label) {
    return '<div class="tc-card tc-stat tc-stat-accent"><div class="tc-stat-value">' + U.esc(value) + '</div><div class="tc-stat-label">' + U.esc(label) + '</div></div>';
  }

  function headHtml(eyebrow, title, dek) {
    return '<div class="tc-view-head">' +
      '<div class="tc-eyebrow upper">' + U.esc(eyebrow) + '</div>' +
      '<h1 class="tc-title">' + U.esc(title) + '</h1>' +
      '<p class="tc-dek">' + U.esc(dek) + '</p>' +
      '</div>';
  }

  /* ======================= view: Match ======================= */

  TC.views.match = {
    render: function (root, param) {
      var patients = TC.data.patients;
      var patientId = param || patients[0].patient_id;
      var basePatient = patients.filter(function (p) { return p.patient_id === patientId; })[0] || patients[0];

      var html = headHtml('Match', 'Ranked shortlist for ' + basePatient.patient_label,
        'Every row shows why it surfaced. Hard facts filter the list; everything from eligibility prose is a labelled suggestion to judge, never a score.');
      html += patientPickerHtml(basePatient.patient_id);
      html += attrPanelHtml(basePatient);
      html += '<div id="tc-match-results"></div>';
      root.innerHTML = html;

      root.querySelectorAll('[data-patient]').forEach(function (btn) {
        U.on(btn, 'click', function () { TC.router.navigate('match', btn.getAttribute('data-patient')); });
      });

      var panel = U.qs('#tc-attr-panel');
      U.on(panel, 'toggle', function () { renderResults(); });
      panel.addEventListener('input', debounce(renderResults, 250));

      function renderResults() {
        var patient = readAttrPanel(basePatient);
        var result = TC.match.matchPatient(patient);
        U.qs('#tc-match-results').innerHTML = matchResultsHtml(result, patient);
        wireMatchLinks();
      }

      function wireMatchLinks() {
        root.querySelectorAll('[data-goto-study]').forEach(function (a) {
          U.on(a, 'click', function (e) {
            e.preventDefault();
            TC.router.navigate('study', a.getAttribute('data-goto-study'), { patient: patientId });
          });
        });
      }

      renderResults();
    }
  };

  function debounce(fn, ms) {
    var t;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  function matchResultsHtml(result, patient) {
    var html = '';
    html += '<div class="tc-section-label">Matches · ' + result.matches.length + '</div>';
    if (!result.matches.length) {
      html += '<div class="tc-empty">No studies pass every hard filter for this profile.</div>';
    }
    result.matches.forEach(function (m) {
      var study = TC.data.studyByNct[m.nct_id];
      if (!study) return;
      html += '<div class="tc-match-row">' +
        '<div>' +
        '<a class="tc-match-title" href="#/study/' + m.nct_id + '" data-goto-study="' + m.nct_id + '">' + U.esc(study.brief_title) + '</a>' +
        '<div class="tc-match-meta">' +
        '<span>' + U.esc(m.nct_id) + '</span>' +
        '<span>' + U.esc(studyPhaseLabel(study)) + '</span>' +
        '<span>' + U.esc(sponsorLabel(study.sponsor_class)) + '</span>' +
        '<span>' + U.esc(m.nearest_site ? (m.nearest_site.facility + ' · ' + cityState(m.nearest_site)) : '') + '</span>' +
        '</div></div>' +
        '<div class="tc-match-dist"><div class="tc-match-dist-value">' + m.distance_mi + '</div><div class="tc-match-dist-label">Miles</div></div>' +
        '<div class="tc-match-chips">' + chipsHtml(m.hard_chips.concat(m.soft_chips)) + '</div>' +
        '</div>';
    });

    html += '<div class="tc-section-label">Near-misses · ' + result.nearMisses.length + '</div>';
    if (!result.nearMisses.length) {
      html += '<div class="tc-empty">No single-criterion near-misses for this profile.</div>';
    }
    result.nearMisses.slice(0, 20).forEach(function (nm) {
      var study = TC.data.studyByNct[nm.nct_id];
      if (!study) return;
      html += '<div class="tc-near-miss-row">' +
        '<div>' +
        '<a class="tc-near-miss-title" href="#/study/' + nm.nct_id + '" data-goto-study="' + nm.nct_id + '">' + U.esc(study.brief_title) + '</a>' +
        '<div class="tc-near-miss-reason">' + U.esc(nm.excluding_reason) + '</div>' +
        '</div>' +
        '<span class="tc-chip tc-chip-hard tc-chip-caution">' + U.esc(nm.excluding_kind) + '</span>' +
        '</div>';
    });
    return html;
  }

  /* ======================= view: The Study ======================= */

  TC.views.study = {
    render: function (root, nctId, query) {
      var study = TC.data.studyByNct[nctId];
      if (!study) { root.innerHTML = '<div class="tc-empty">Trial not found in this snapshot.</div>'; return; }

      var patientId = (query && query.patient) || TC.data.patients[0].patient_id;
      var patient = TC.data.patients.filter(function (p) { return p.patient_id === patientId; })[0];
      var sites = TC.data.sitesByNct[nctId] || [];
      var signals = TC.data.signalsByNct[nctId] || [];

      var html = '<div class="tc-view-head">' +
        '<div class="tc-eyebrow upper">' + U.esc(nctId) + ' · ' + U.esc(studyPhaseLabel(study)) + ' · ' + U.esc(sponsorLabel(study.sponsor_class)) + '</div>' +
        '<h1 class="tc-title">' + U.esc(study.brief_title) + '</h1>' +
        '<p class="editorial">' + U.esc(study.official_title) + '</p>' +
        '</div>';

      var matchInfo = null;
      if (patient) {
        var result = TC.match.matchPatient(patient);
        matchInfo = result.matches.filter(function (m) { return m.nct_id === nctId; })[0] ||
          { nearMiss: result.nearMisses.filter(function (m) { return m.nct_id === nctId; })[0] };
      }

      html += '<div class="tc-section-label">This patient · ' + U.esc(patient ? patient.patient_label : '') + '</div>';
      html += '<div class="tc-card">';
      if (matchInfo && matchInfo.hard_chips) {
        html += '<div class="upper tc-chip-match" style="font-size:11px;margin-bottom:10px;">On the shortlist</div>';
        html += chipsHtml(matchInfo.hard_chips.concat(matchInfo.soft_chips));
      } else if (matchInfo && matchInfo.nearMiss) {
        html += '<div class="upper" style="font-size:11px;margin-bottom:10px;color:var(--terracotta);">Near-miss</div>';
        html += '<div class="tc-near-miss-reason">' + U.esc(matchInfo.nearMiss.excluding_reason) + '</div>';
      } else {
        html += '<div class="tc-muted">Not in range for this profile’s tumor type.</div>';
      }
      html += '</div>';

      html += '<div class="tc-section-label">Eligibility criteria (verbatim)</div>';
      html += '<div class="tc-criteria-block">' + highlightCriteria(study.eligibility_criteria, signals) + '</div>';

      html += '<div class="tc-section-label">US recruiting sites · ' + sites.length + '</div>';
      html += '<div class="tc-scroll-x"><table class="tc-sites-table"><thead><tr>' +
        '<th>Facility</th><th>City / State</th><th>Status</th><th>Distance</th><th>Contact</th>' +
        '</tr></thead><tbody>';
      var sitesWithDist = sites.map(function (s) {
        var d = (patient && s.lat != null) ? U.round1(U.haversineMi(patient.home_lat, patient.home_lon, s.lat, s.lon)) : null;
        return { s: s, d: d };
      }).sort(function (a, b) { return (a.d == null ? 1e9 : a.d) - (b.d == null ? 1e9 : b.d); });
      sitesWithDist.forEach(function (row) {
        var s = row.s;
        var dotCls = s.status === 'RECRUITING' ? 'recruiting' : 'other';
        html += '<tr>' +
          '<td>' + U.esc(s.facility) + '</td>' +
          '<td>' + U.esc(cityState(s)) + '</td>' +
          '<td><span class="tc-status-dot ' + dotCls + '"></span>' + U.esc(s.status) + '</td>' +
          '<td>' + (row.d != null ? row.d + ' mi' : '–') + '</td>' +
          '<td>' + (s.contact_phone ? U.esc(s.contact_phone) : (s.contact_email ? U.esc(s.contact_email) : '–')) + '</td>' +
          '</tr>';
      });
      html += '</tbody></table></div>';

      root.innerHTML = html;
    }
  };

  function highlightCriteria(text, signals) {
    var esc = U.esc(text || '');
    var sentences = [];
    signals.forEach(function (s) { if (s.source_sentence) sentences.push(s.source_sentence); });
    sentences.sort(function (a, b) { return b.length - a.length; });
    sentences.forEach(function (sentence) {
      var escSentence = U.esc(sentence);
      if (!escSentence) return;
      var idx = esc.indexOf(escSentence);
      if (idx === -1) return;
      esc = esc.slice(0, idx) + '<mark>' + escSentence + '</mark>' + esc.slice(idx + escSentence.length);
    });
    return esc;
  }

  /* ======================= view: The Map ======================= */

  TC.views.map = {
    render: function (root, param) {
      var patients = TC.data.patients;
      var patientId = param || patients[0].patient_id;
      var patient = patients.filter(function (p) { return p.patient_id === patientId; })[0] || patients[0];

      var html = headHtml('The map', 'Recruiting sites near ' + patient.patient_label,
        'Distance rings at 25, 50 and 100 miles. Travel burden is the top enrollment killer. This is a schematic view, not a routing tool.');
      html += patientPickerHtml(patient.patient_id);

      var relevantStudies = TC.data.studies.filter(function (s) { return s.tumor_types_matched.indexOf(patient.tumor_type) !== -1; });
      var nctSet = {};
      relevantStudies.forEach(function (s) { nctSet[s.nct_id] = true; });
      var sites = TC.data.sites.filter(function (s) { return nctSet[s.nct_id] && s.x != null; });

      html += '<div class="tc-map-wrap"><svg viewBox="' + TC.data.states.viewBox + '" style="width:100%;height:auto;">';
      var statePaths = TC.data.states.states;
      Object.keys(statePaths).forEach(function (name) {
        html += '<path class="tc-map-state" d="' + statePaths[name] + '"></path>';
      });
      [patient.ring_25_r, patient.ring_50_r, patient.ring_100_r].forEach(function (r) {
        if (r != null) html += '<circle class="tc-map-ring" cx="' + patient.x + '" cy="' + patient.y + '" r="' + r + '"></circle>';
      });
      sites.forEach(function (s) {
        var cls = 'tc-map-site' + (s.status === 'RECRUITING' ? ' recruiting' : '');
        html += '<circle class="' + cls + '" cx="' + s.x + '" cy="' + s.y + '" r="3.2"><title>' + U.esc(s.facility + ' · ' + cityState(s)) + '</title></circle>';
      });
      html += '<circle class="tc-map-patient" cx="' + patient.x + '" cy="' + patient.y + '" r="6"><title>' + U.esc(patient.patient_label + ' (home ZIP)') + '</title></circle>';
      html += '</svg></div>';

      html += '<div class="tc-map-legend">' +
        '<span><span class="tc-map-legend-swatch" style="background:var(--accent);"></span>Patient home ZIP</span>' +
        '<span><span class="tc-map-legend-swatch" style="background:var(--ocotillo);"></span>Recruiting site</span>' +
        '<span><span class="tc-map-legend-swatch" style="background:var(--turquoise);"></span>Other status</span>' +
        '<span>Dashed rings: 25 / 50 / 100 mi (schematic)</span>' +
        '</div>';

      html += '<p class="tc-muted tc-mt" style="font-size:12.5px;">' + sites.length + ' US sites shown for ' + TUMOR_LABELS[patient.tumor_type] + ' studies with a geocoded location.</p>';

      root.innerHTML = html;
      root.querySelectorAll('[data-patient]').forEach(function (btn) {
        U.on(btn, 'click', function () { TC.router.navigate('map', btn.getAttribute('data-patient')); });
      });
    }
  };

  /* ======================= view: The Landscape ======================= */

  TC.views.landscape = {
    render: function (root) {
      var L = TC.data.landscape;
      var html = headHtml('The landscape', 'Where the network’s options are thin',
        'Recruiting studies by tumor type, phase and sponsor class: lookup turned into strategy.');

      html += '<div class="tc-grid tc-grid-2 tc-mt">';
      html += '<div class="tc-card"><div class="tc-section-label" style="margin-top:0;">By phase</div>' + barList(L.by_phase, phaseLabel) + '</div>';
      html += '<div class="tc-card"><div class="tc-section-label" style="margin-top:0;">By sponsor class</div>' + barList(L.by_sponsor_class, sponsorLabel) + '</div>';
      html += '</div>';

      html += '<div class="tc-section-label">Tumor type × phase × sponsor</div>';
      var phases = Object.keys(L.by_phase).sort();
      html += '<div class="tc-scroll-x"><table class="tc-matrix-table"><thead><tr><th style="text-align:left;">Tumor type</th>';
      phases.forEach(function (ph) { html += '<th>' + U.esc(phaseLabel(ph)) + '</th>'; });
      html += '<th>Total</th></tr></thead><tbody>';

      Object.keys(TUMOR_LABELS).forEach(function (t) {
        var total = L.by_tumor_type[t] || 0;
        html += '<tr><td class="tc-matrix-row-label">' + TUMOR_LABELS[t] + '</td>';
        phases.forEach(function (ph) {
          var cell = L.matrix.filter(function (m) { return m.tumor_type === t && m.phase === ph; })[0];
          var count = cell ? cell.count : 0;
          var cls = count === 0 ? 'tc-matrix-thin' : 'tc-matrix-cell';
          html += '<td class="' + cls + '">' + (count || '–') + '</td>';
        });
        html += '<td class="tc-matrix-cell"><strong>' + total + '</strong></td></tr>';
      });
      html += '</tbody></table></div>';

      root.innerHTML = html;
    }
  };

  function phaseLabel(p) {
    if (p === 'NA') return 'Not applicable';
    return p.replace('PHASE', 'Phase ').replace('EARLY_', 'Early ');
  }

  function barList(counts, labelFn) {
    var max = Math.max.apply(null, Object.keys(counts).map(function (k) { return counts[k]; }));
    var keys = Object.keys(counts).sort(function (a, b) { return counts[b] - counts[a]; });
    var html = '';
    keys.forEach(function (k) {
      var pct = max ? (counts[k] / max * 100) : 0;
      html += '<div class="tc-bar-row">' +
        '<div class="tc-bar-label">' + U.esc(labelFn(k)) + '</div>' +
        '<div class="tc-bar-track"><div class="tc-bar-fill" style="width:' + pct + '%"></div></div>' +
        '<div class="tc-bar-value">' + counts[k] + '</div>' +
        '</div>';
    });
    return html;
  }

  /* ======================= app shell / init ======================= */

  function setActiveNav(view) {
    document.querySelectorAll('#tc-nav a').forEach(function (a) {
      a.classList.toggle('active', a.getAttribute('data-view') === view);
    });
  }

  function renderRoute() {
    var r = TC.router.parse();
    setActiveNav(r.view);
    var root = U.qs('#tc-view-root');
    var view = TC.views[r.view] || TC.views.gap;
    if (r.view === 'study') view.render(root, r.param, r.query);
    else view.render(root, r.param, r.query);
    window.scrollTo(0, 0);
  }

  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem('tc-theme'); } catch (e) {}
    var theme = saved || 'dark';
    document.documentElement.setAttribute('data-theme', theme);
    updateThemeLabel(theme);
    U.on(U.qs('#tc-theme-toggle'), 'click', function () {
      var cur = document.documentElement.getAttribute('data-theme');
      var next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      updateThemeLabel(next);
      try { localStorage.setItem('tc-theme', next); } catch (e) {}
    });
  }

  function updateThemeLabel(theme) {
    U.qs('#tc-theme-label').textContent = theme === 'dark' ? 'Light' : 'Dark';
  }

  function initFooter() {
    var el = U.qs('#tc-footer-snapshot');
    if (el && TC.data.meta.snapshot_date) el.textContent = 'Trial data as of ' + TC.data.meta.snapshot_date;
  }

  function init() {
    initTheme();
    TC.data.load().then(function () {
      initFooter();
      renderRoute();
      window.addEventListener('hashchange', renderRoute);
    }).catch(function (err) {
      U.qs('#tc-view-root').innerHTML = '<div class="tc-empty">Could not load trial data. ' + U.esc(err && err.message) + '</div>';
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
