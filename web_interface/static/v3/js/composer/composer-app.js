/**
 * composerApp() — AlpineJS component for the Plugin Composer.
 *
 * Key architecture notes:
 *   - All mutations call _snapshot() for undo/redo support
 *   - Elements with anchors: drag stores offset from anchor, not raw px
 *   - localStorage autosaves on every mutation (debounced 1.5s)
 *   - composer_version in payload allows future server-side migration
 */

// ── Template library ─────────────────────────────────────────────────────────
const COMPOSER_TEMPLATES = [
  {
    id: 'blank',
    label: 'Blank Canvas',
    description: 'Start from scratch',
    icon: 'fas fa-plus-square',
    preset: '128×32',
    elements: [],
    dataModel: { configVars: [], dataSources: [], computedVars: [] },
  },
  {
    id: 'clock',
    label: 'Digital Clock',
    description: 'Large clock with date below a divider',
    icon: 'fas fa-clock',
    preset: '128×32',
    elements: [
      { id:1, type:'clock', x:44, y:4, format:'%H:%M', font:'press_start', r:100, g:255, b:100, xAnchor:null, yAnchor:null, minWidth:0, label:'time', conditions:[] },
      { id:2, type:'divider', x:0, y:18, orientation:'horizontal', r:50, g:50, b:50, xAnchor:null, yAnchor:null, minWidth:0, label:'', conditions:[] },
      { id:3, type:'clock', x:32, y:22, format:'%A  %b %d', font:'four_by_six', r:150, g:150, b:150, xAnchor:null, yAnchor:null, minWidth:0, label:'date', conditions:[] },
    ],
    dataModel: { configVars:[], dataSources:[], computedVars:[] },
  },
  {
    id: 'announcement',
    label: 'Announcement',
    description: 'Static title + configurable message text',
    icon: 'fas fa-bullhorn',
    preset: '128×32',
    elements: [
      { id:1, type:'text', x:4, y:2, text:'ANNOUNCEMENT', font:'four_by_six', r:255, g:200, b:0, xAnchor:null, yAnchor:null, minWidth:0, label:'title', conditions:[] },
      { id:2, type:'divider', x:0, y:11, orientation:'horizontal', r:80, g:80, b:0, xAnchor:null, yAnchor:null, minWidth:0, label:'', conditions:[] },
      { id:3, type:'dynamic_text', x:4, y:15, binding:{source:'config',key:'message',format:null}, font:'four_by_six', r:255, g:255, b:255, xAnchor:null, yAnchor:null, minWidth:0, label:'message', conditions:[] },
    ],
    dataModel: {
      configVars:[{ key:'message', label:'Message', type:'string', default:'Hello World!', description:'Message to display' }],
      dataSources:[], computedVars:[],
    },
  },
  {
    id: 'scoreboard',
    label: 'Scoreboard',
    description: 'Two-team scores with status line',
    icon: 'fas fa-trophy',
    preset: '128×32',
    elements: [
      { id:1, type:'dynamic_text', x:2, y:2,  binding:{source:'config',key:'home_team',format:null}, font:'four_by_six', r:255,g:255,b:255, xAnchor:null,yAnchor:null,minWidth:0, label:'home team', conditions:[] },
      { id:2, type:'dynamic_text', x:2, y:12, binding:{source:'config',key:'home_score',format:null}, font:'press_start', r:255,g:220,b:50, xAnchor:null,yAnchor:null,minWidth:0, label:'home score', conditions:[] },
      { id:3, type:'divider', x:64, y:0, orientation:'vertical', r:60,g:60,b:60, xAnchor:null,yAnchor:null,minWidth:0, label:'', conditions:[] },
      { id:4, type:'dynamic_text', x:68, y:2,  binding:{source:'config',key:'away_team',format:null}, font:'four_by_six', r:200,g:200,b:255, xAnchor:null,yAnchor:null,minWidth:0, label:'away team', conditions:[] },
      { id:5, type:'dynamic_text', x:68, y:12, binding:{source:'config',key:'away_score',format:null}, font:'press_start', r:100,g:180,b:255, xAnchor:null,yAnchor:null,minWidth:0, label:'away score', conditions:[] },
      { id:6, type:'dynamic_text', x:44, y:25, binding:{source:'config',key:'game_status',format:null}, font:'four_by_six', r:150,g:150,b:150, xAnchor:null,yAnchor:null,minWidth:0, label:'status', conditions:[] },
    ],
    dataModel: {
      configVars:[
        {key:'home_team',  label:'Home Team',   type:'string', default:'HOME', description:'Home team abbreviation'},
        {key:'home_score', label:'Home Score',  type:'string', default:'0',    description:'Home team score'},
        {key:'away_team',  label:'Away Team',   type:'string', default:'AWAY', description:'Away team abbreviation'},
        {key:'away_score', label:'Away Score',  type:'string', default:'0',    description:'Away team score'},
        {key:'game_status',label:'Game Status', type:'string', default:'LIVE', description:'Status label'},
      ],
      dataSources:[], computedVars:[],
    },
  },
  {
    id: 'weather',
    label: 'Weather Card',
    description: 'Temperature + condition + location',
    icon: 'fas fa-cloud-sun',
    preset: '128×32',
    elements: [
      { id:1, type:'dynamic_text', x:2, y:4,  binding:{source:'config',key:'temperature',format:null}, font:'press_start', r:255,g:140,b:40, xAnchor:null,yAnchor:null,minWidth:0, label:'temp', conditions:[] },
      { id:2, type:'divider', x:0, y:16, orientation:'horizontal', r:50,g:50,b:80, xAnchor:null,yAnchor:null,minWidth:0, label:'', conditions:[] },
      { id:3, type:'dynamic_text', x:2, y:20,  binding:{source:'config',key:'condition',format:null}, font:'four_by_six', r:180,g:200,b:255, xAnchor:null,yAnchor:null,minWidth:0, label:'condition', conditions:[] },
      { id:4, type:'dynamic_text', x:80, y:20, binding:{source:'config',key:'location',format:null}, font:'four_by_six', r:120,g:120,b:120, xAnchor:null,yAnchor:null,minWidth:0, label:'location', conditions:[] },
    ],
    dataModel: {
      configVars:[
        {key:'temperature', label:'Temperature', type:'string', default:'72°F',   description:'Current temperature'},
        {key:'condition',   label:'Condition',   type:'string', default:'Sunny',  description:'Weather condition'},
        {key:'location',    label:'Location',    type:'string', default:'My City', description:'City name'},
      ],
      dataSources:[], computedVars:[],
    },
  },
  {
    id: 'crypto',
    label: 'Crypto Ticker',
    description: 'Asset price + 24h change percentage',
    icon: 'fas fa-chart-line',
    preset: '128×32',
    elements: [
      { id:1, type:'dynamic_text', x:2, y:2,  binding:{source:'config',key:'ticker',format:null}, font:'press_start', r:255,g:165,b:0, xAnchor:null,yAnchor:null,minWidth:0, label:'ticker', conditions:[] },
      { id:2, type:'dynamic_text', x:2, y:16, binding:{source:'config',key:'price',format:null}, font:'press_start', r:255,g:255,b:255, xAnchor:null,yAnchor:null,minWidth:0, label:'price', conditions:[] },
      { id:3, type:'dynamic_text', x:88, y:16, binding:{source:'config',key:'change_pct',format:null}, font:'four_by_six', r:100,g:255,b:100, xAnchor:null,yAnchor:null,minWidth:0, label:'change', conditions:[] },
    ],
    dataModel: {
      configVars:[
        {key:'ticker',     label:'Ticker',    type:'string', default:'BTC',      description:'Asset symbol'},
        {key:'price',      label:'Price',     type:'string', default:'$0.00',    description:'Asset price'},
        {key:'change_pct', label:'Change %',  type:'string', default:'+0.00%',   description:'24h change'},
      ],
      dataSources:[], computedVars:[],
    },
  },
  {
    id: 'system',
    label: 'System Monitor',
    description: 'CPU, memory, and IP address',
    icon: 'fas fa-server',
    preset: '128×32',
    elements: [
      { id:1, type:'text', x:2, y:2,  text:'CPU', font:'four_by_six', r:120,g:120,b:120, xAnchor:null,yAnchor:null,minWidth:0, label:'', conditions:[] },
      { id:2, type:'dynamic_text', x:22, y:2,  binding:{source:'config',key:'cpu_usage',format:null}, font:'four_by_six', r:100,g:255,b:100, xAnchor:null,yAnchor:null,minWidth:0, label:'cpu', conditions:[] },
      { id:3, type:'text', x:2, y:12, text:'MEM', font:'four_by_six', r:120,g:120,b:120, xAnchor:null,yAnchor:null,minWidth:0, label:'', conditions:[] },
      { id:4, type:'dynamic_text', x:22, y:12, binding:{source:'config',key:'mem_usage',format:null}, font:'four_by_six', r:100,g:180,b:255, xAnchor:null,yAnchor:null,minWidth:0, label:'mem', conditions:[] },
      { id:5, type:'text', x:2, y:22, text:'IP', font:'four_by_six', r:120,g:120,b:120, xAnchor:null,yAnchor:null,minWidth:0, label:'', conditions:[] },
      { id:6, type:'dynamic_text', x:14, y:22, binding:{source:'config',key:'ip_address',format:null}, font:'four_by_six', r:255,g:255,b:150, xAnchor:null,yAnchor:null,minWidth:0, label:'ip', conditions:[] },
    ],
    dataModel: {
      configVars:[
        {key:'cpu_usage',  label:'CPU %',    type:'string', default:'0%',      description:'CPU usage'},
        {key:'mem_usage',  label:'Memory %', type:'string', default:'0%',      description:'Memory usage'},
        {key:'ip_address', label:'IP Address',type:'string', default:'0.0.0.0', description:'Network IP'},
      ],
      dataSources:[], computedVars:[],
    },
  },
  {
    id: 'split_panel',
    label: 'Split Panel',
    description: 'Left label + right value, divided',
    icon: 'fas fa-columns',
    preset: '128×32',
    elements: [
      { id:1, type:'dynamic_text', x:2, y:4, binding:{source:'config',key:'left_label',format:null}, font:'four_by_six', r:180,g:180,b:180, xAnchor:null,yAnchor:null,minWidth:0, label:'label', conditions:[] },
      { id:2, type:'divider', x:64, y:0, orientation:'vertical', r:60,g:60,b:60, xAnchor:null,yAnchor:null,minWidth:0, label:'', conditions:[] },
      { id:3, type:'dynamic_text', x:68, y:4, binding:{source:'config',key:'right_value',format:null}, font:'press_start', r:255,g:255,b:255, xAnchor:null,yAnchor:null,minWidth:0, label:'value', conditions:[] },
    ],
    dataModel: {
      configVars:[
        {key:'left_label',  label:'Label',  type:'string', default:'LABEL', description:'Left panel label'},
        {key:'right_value', label:'Value',  type:'string', default:'--',    description:'Right panel value'},
      ],
      dataSources:[], computedVars:[],
    },
  },
];

// ── LED display color palette ────────────────────────────────────────────────
const LED_PALETTE = [
  { label: 'White',      hex: '#ffffff', r: 255, g: 255, b: 255 },
  { label: 'Red',        hex: '#ff2020', r: 255, g: 32,  b: 32  },
  { label: 'Green',      hex: '#20ff40', r: 32,  g: 255, b: 64  },
  { label: 'Blue',       hex: '#4060ff', r: 64,  g: 96,  b: 255 },
  { label: 'Yellow',     hex: '#ffee00', r: 255, g: 238, b: 0   },
  { label: 'Orange',     hex: '#ff8000', r: 255, g: 128, b: 0   },
  { label: 'Cyan',       hex: '#00eeff', r: 0,   g: 238, b: 255 },
  { label: 'Magenta',    hex: '#ff00cc', r: 255, g: 0,   b: 204 },
  { label: 'Amber',      hex: '#ffb400', r: 255, g: 180, b: 0   },
  { label: 'Dim white',  hex: '#888888', r: 136, g: 136, b: 136 },
  { label: 'Dark gray',  hex: '#444444', r: 68,  g: 68,  b: 68  },
  { label: 'Off/Black',  hex: '#000000', r: 0,   g: 0,   b: 0   },
];

// ── Autosave helpers ─────────────────────────────────────────────────────────
const LS_KEY = 'ledmatrix_composer_draft';
let _autosaveTimer = null;

function _debouncedAutosave(payload) {
  clearTimeout(_autosaveTimer);
  _autosaveTimer = setTimeout(() => {
    try { localStorage.setItem(LS_KEY, JSON.stringify(payload)); } catch (_) {}
  }, 1500);
}

// ── Main component ───────────────────────────────────────────────────────────
function composerApp() {
  return {
    // ── Plugin metadata ───────────────────────────────────────────────
    metadata: {
      id: '',
      name: '',
      author: '',
      version: '1.0.0',
      description: '',
      category: 'custom',
      display_duration: 15,
      update_interval: 60,
      api_requirements: [],
      bgColor: { r: 0, g: 0, b: 0 },
    },

    // ── Canvas / display preset ───────────────────────────────────────
    SCALE: 4,
    MATRIX_W: 128,
    MATRIX_H: 32,
    currentPreset: '128×32',

    // ── Elements ──────────────────────────────────────────────────────
    elements: [],
    _nextId: 1,

    // ── Data model ───────────────────────────────────────────────────
    dataModel: {
      configVars: [],
      dataSources: [],
      computedVars: [],
    },

    // ── Undo/redo ─────────────────────────────────────────────────────
    _history: [],
    _historyIndex: -1,

    // ── Selection & interaction ───────────────────────────────────────
    selectedId: null,
    _drag:   { active: false },
    _resize: { active: false },

    // ── Canvas options ────────────────────────────────────────────────
    snapToGrid: false,
    snapSize: 4,

    // ── Clipboard ─────────────────────────────────────────────────────
    _clipboard: null,
    _styleClipboard: null,

    // ── UI state ──────────────────────────────────────────────────────
    isDirty: false,
    statusMsg: '',
    statusType: 'info',
    generateStatus: 'idle',
    installStatus: 'idle',
    showGridOverlay: true,
    showGuides: false,
    blinkAnimating: false,
    _blinkPhase: false,
    _blinkTimer: null,
    _animTick: 0,
    _previewValues: {},
    showHelpModal: false,
    _recentColors: [],
    showRuler: false,
    showConfigVarModal: false,
    showOpenModal: false,
    showTemplateModal: false,
    showCodeModal: false,
    _idAutoGenerated: true,
    hoverInfo: '',
    installedPlugins: [],
    loadPluginsStatus: 'idle',
    codeFiles: {},
    codeTab: 'manager.py',
    loadingCode: false,
    newConfigVar: { key: '', label: '', type: 'string', default: '', description: '' },

    // ── Computed ──────────────────────────────────────────────────────
    get selectedElement() {
      return this.elements.find(e => e.id === this.selectedId) ?? null;
    },
    get canExport() {
      return this.metadata.name.trim() && this.metadata.id.trim() && this.elements.length > 0;
    },
    get canUndo() { return this._historyIndex > 0; },
    get canRedo() { return this._historyIndex < this._history.length - 1; },
    get templates() { return COMPOSER_TEMPLATES; },
    get displayPresets() { return window.ComposerCanvas.DISPLAY_PRESETS; },
    get palette() { return LED_PALETTE; },

    // ── Pre-made variable library ─────────────────────────────────────
    preMadeVarCategories: [
      { label: 'Display', vars: [
          {key:'title_text', label:'Title',    type:'string', default:'My Plugin', description:'Main title'},
          {key:'subtitle',   label:'Subtitle', type:'string', default:'',          description:'Secondary line'},
          {key:'message',    label:'Message',  type:'string', default:'Hello!',    description:'Dynamic message'},
      ]},
      { label: 'Sports / Score', vars: [
          {key:'home_score',  label:'Home Score',  type:'string', default:'0',    description:'Home team score'},
          {key:'away_score',  label:'Away Score',  type:'string', default:'0',    description:'Away team score'},
          {key:'home_team',   label:'Home Team',   type:'string', default:'HOME', description:'Home team abbr'},
          {key:'away_team',   label:'Away Team',   type:'string', default:'AWAY', description:'Away team abbr'},
          {key:'game_status', label:'Game Status', type:'string', default:'LIVE', description:'Status label'},
          {key:'period',      label:'Period',      type:'string', default:'1st',  description:'Period/quarter'},
          {key:'time_left',   label:'Time Left',   type:'string', default:'15:00',description:'Game clock'},
      ]},
      { label: 'Weather', vars: [
          {key:'temperature', label:'Temperature', type:'string', default:'72°F',   description:'Current temperature'},
          {key:'condition',   label:'Condition',   type:'string', default:'Sunny',  description:'Weather condition'},
          {key:'humidity',    label:'Humidity',    type:'string', default:'50%',    description:'Humidity'},
          {key:'location',    label:'Location',    type:'string', default:'My City',description:'Location name'},
      ]},
      { label: 'System / Stats', vars: [
          {key:'cpu_usage',  label:'CPU %',     type:'string', default:'0%',      description:'CPU usage'},
          {key:'mem_usage',  label:'Memory %',  type:'string', default:'0%',      description:'Memory usage'},
          {key:'ip_address', label:'IP Address',type:'string', default:'0.0.0.0', description:'Network IP'},
          {key:'uptime',     label:'Uptime',    type:'string', default:'0d 0h',   description:'System uptime'},
      ]},
      { label: 'Crypto / Finance', vars: [
          {key:'ticker',     label:'Ticker',   type:'string', default:'BTC',    description:'Asset symbol'},
          {key:'price',      label:'Price',    type:'string', default:'$0.00',  description:'Asset price'},
          {key:'change_pct', label:'Change %', type:'string', default:'+0.00%', description:'24h change'},
          {key:'volume',     label:'Volume',   type:'string', default:'0',      description:'Trading volume'},
      ]},
    ],

    elementTypes: [
      { id: 'text',         label: 'Text',         icon: 'fas fa-font' },
      { id: 'dynamic_text', label: 'Dynamic Text',  icon: 'fas fa-code' },
      { id: 'clock',        label: 'Clock',         icon: 'fas fa-clock' },
      { id: 'rectangle',         label: 'Rectangle',    icon: 'fas fa-square' },
      { id: 'rounded_rectangle', label: 'Rounded Rect', icon: 'fas fa-stop' },
      { id: 'ellipse',           label: 'Ellipse',      icon: 'fas fa-circle' },
      { id: 'arc',               label: 'Arc',          icon: 'fas fa-circle-notch' },
      { id: 'pixel',             label: 'Pixel',        icon: 'fas fa-dot-circle' },
      { id: 'line',              label: 'Line',         icon: 'fas fa-minus' },
      { id: 'divider',      label: 'Divider',       icon: 'fas fa-grip-lines' },
      { id: 'progress_bar', label: 'Progress Bar',  icon: 'fas fa-tasks' },
      { id: 'countdown',    label: 'Countdown',     icon: 'fas fa-hourglass-half' },
      { id: 'marquee',      label: 'Marquee',       icon: 'fas fa-arrows-alt-h' },
      { id: 'gauge',        label: 'Gauge',         icon: 'fas fa-tachometer-alt' },
      { id: 'sparkline',    label: 'Sparkline',     icon: 'fas fa-chart-bar' },
      { id: 'pips',         label: 'Pips / Rating',  icon: 'fas fa-star-half-alt' },
      { id: 'section',      label: 'Section Label',  icon: 'fas fa-tag' },
    ],

    COLOR_THEMES: [
      { label: 'Matrix',  colors: [{ r:0,g:255,b:70 },  { r:0,g:180,b:50 },  { r:0,g:100,b:30 }]  },
      { label: 'Neon',    colors: [{ r:255,g:0,b:200 }, { r:0,g:240,b:255 }, { r:255,g:230,b:0 }]  },
      { label: 'Warm',    colors: [{ r:255,g:160,b:0 }, { r:255,g:80,b:0 },  { r:255,g:220,b:80 }] },
      { label: 'Arctic',  colors: [{ r:100,g:200,b:255},{ r:150,g:230,b:255},{ r:200,g:240,b:255}] },
      { label: 'Retro',   colors: [{ r:255,g:140,b:0 }, { r:255,g:60,b:60 }, { r:80,g:200,b:120 }] },
      { label: 'Mono',    colors: [{ r:255,g:255,b:255},{ r:170,g:170,b:170},{ r:90,g:90,b:90 }]   },
    ],

    // ── Lifecycle ─────────────────────────────────────────────────────
    init() {
      this.$nextTick(() => {
        const canvas = document.getElementById('led-canvas');
        if (canvas) {
          window.ComposerCanvas.init(canvas);
          window.ComposerCanvas.updateCanvasSize(this.MATRIX_W, this.MATRIX_H, this.SCALE);
          this._loadFonts().then(() => {
            this._tryRestoreDraft();
            this._loadRecentColors();
            this._snapshot();
            this.render();
          });
        }
        document.addEventListener('keydown', (e) => this._onKeyDown(e));
      });
    },

    async _loadFonts() {
      try {
        const ff = new FontFace('PressStart2P', 'url(/composer/api/fonts/PressStart2P-Regular.ttf)');
        document.fonts.add(await ff.load());
      } catch (e) {
        console.warn('PressStart2P load failed:', e);
      }
    },

    _loadRecentColors() {
      try {
        const raw = localStorage.getItem('ledmatrix_composer_recent_colors');
        if (raw) this._recentColors = JSON.parse(raw).slice(0, 8);
      } catch (_) {}
    },

    _trackColor(r, g, b) {
      this._recentColors = [
        { r, g, b },
        ...this._recentColors.filter(c => !(c.r === r && c.g === g && c.b === b)),
      ].slice(0, 8);
      try { localStorage.setItem('ledmatrix_composer_recent_colors', JSON.stringify(this._recentColors)); } catch (_) {}
    },

    toggleRuler() {
      this.showRuler = !this.showRuler;
      this.render();
    },

    _tryRestoreDraft() {
      try {
        const raw = localStorage.getItem(LS_KEY);
        if (!raw) return;
        const draft = JSON.parse(raw);
        if (!draft?.composer_version) return;
        if (!confirm('Resume your last unsaved session?')) {
          localStorage.removeItem(LS_KEY);
          return;
        }
        this._applyState(draft);
        this._setStatus('Draft restored from last session', 'info');
      } catch (_) {}
    },

    // ── Undo / Redo ───────────────────────────────────────────────────
    _snapshot() {
      const state = JSON.stringify({
        metadata: this.metadata,
        elements: this.elements,
        dataModel: this.dataModel,
        currentPreset: this.currentPreset,
      });
      this._history.splice(this._historyIndex + 1);
      this._history.push(state);
      if (this._history.length > 50) this._history.shift();
      else this._historyIndex++;
      _debouncedAutosave(this._buildPayload());
    },

    undo() {
      if (!this.canUndo) return;
      this._historyIndex--;
      const state = JSON.parse(this._history[this._historyIndex]);
      this._applyState(state);
      this._setStatus('Undo', 'info');
      this.render();
    },

    redo() {
      if (!this.canRedo) return;
      this._historyIndex++;
      const state = JSON.parse(this._history[this._historyIndex]);
      this._applyState(state);
      this._setStatus('Redo', 'info');
      this.render();
    },

    _applyState(state) {
      if (state.metadata)   this.metadata   = state.metadata;
      if (state.elements)   this.elements   = state.elements.map(el => ({
        xAnchor: null, yAnchor: null, minWidth: 0, conditions: [],
        ...el,
        id: el.id ?? (this._nextId++),
      }));
      if (state.dataModel)  this.dataModel  = state.dataModel;
      if (state.currentPreset && state.currentPreset !== this.currentPreset) {
        this.changePreset(state.currentPreset, { silent: true });
      }
      this._nextId = Math.max(...this.elements.map(e => e.id + 1), 1);
      this.selectedId = null;
      this.isDirty = true;
    },

    // ── Display preset ────────────────────────────────────────────────
    changePreset(presetLabel, opts = {}) {
      const preset = window.ComposerCanvas.DISPLAY_PRESETS.find(p => p.label === presetLabel);
      if (!preset) return;
      this.currentPreset = presetLabel;
      this.MATRIX_W = preset.w;
      this.MATRIX_H = preset.h;
      this.SCALE = preset.w <= 64 ? 6 : preset.w <= 128 ? 4 : 2;
      const canvas = document.getElementById('led-canvas');
      if (canvas) {
        window.ComposerCanvas.updateCanvasSize(this.MATRIX_W, this.MATRIX_H, this.SCALE);
        canvas.style.width  = (this.MATRIX_W * this.SCALE) + 'px';
        canvas.style.height = (this.MATRIX_H * this.SCALE) + 'px';
      }
      if (!opts.silent) this.render();
    },

    // ── Rendering ─────────────────────────────────────────────────────
    render(opts = {}) {
      window.ComposerCanvas.render(
        this.elements, this.selectedId, this.MATRIX_W, this.MATRIX_H, this.SCALE,
        { bgColor: this.metadata.bgColor, showGuides: this.showGuides, showRuler: this.showRuler, blinkOff: this.blinkAnimating && this._blinkPhase, animTick: this.blinkAnimating ? this._animTick : null, previewValues: this._previewValues, ...opts },
      );
    },

    toggleGrid() {
      this.showGridOverlay = !this.showGridOverlay;
      window.ComposerCanvas.setGrid(this.showGridOverlay);
      this.render();
    },

    toggleGuides() {
      this.showGuides = !this.showGuides;
      this.render();
    },

    toggleBlinkPreview() {
      this.blinkAnimating = !this.blinkAnimating;
      if (this.blinkAnimating) {
        this._blinkPhase = false;
        this._animTick = 0;
        let blinkTick = 0;
        this._blinkTimer = setInterval(() => {
          this._animTick++;
          blinkTick++;
          if (blinkTick >= 3) { this._blinkPhase = !this._blinkPhase; blinkTick = 0; }
          this.render();
        }, 200);
      } else {
        clearInterval(this._blinkTimer);
        this._blinkTimer = null;
        this._blinkPhase = false;
        this._animTick = 0;
        this.render();
      }
    },

    downloadPng() {
      const canvas = document.getElementById('led-canvas');
      if (!canvas) return;
      const link = document.createElement('a');
      link.download = (this.metadata.id || 'composer-preview') + '.png';
      link.href = canvas.toDataURL('image/png');
      link.click();
    },

    exportDesign() {
      const payload = this._buildPayload();
      const json = JSON.stringify(payload, null, 2);
      const blob = new Blob([json], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.download = (this.metadata.id || 'composer-design') + '.composer.json';
      link.href = url;
      link.click();
      URL.revokeObjectURL(url);
    },

    importDesign() {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.json,.composer.json';
      input.onchange = (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (ev) => {
          try {
            const data = JSON.parse(ev.target.result);
            if (!data.composer_version) throw new Error('Not a composer file');
            if (this.isDirty && !confirm('Replace current design?')) return;
            this._applyState({ metadata: data.metadata, elements: data.elements, dataModel: data.dataModel });
            this.isDirty = false;
            this._setStatus('Design loaded', 'success');
            this.render();
          } catch (err) {
            this._setStatus('Failed to load: ' + err.message, 'error');
          }
        };
        reader.readAsText(file);
      };
      input.click();
    },

    // ── Zoom controls ─────────────────────────────────────────────────
    zoomIn() {
      if (this.SCALE >= 8) return;
      this.SCALE++;
      this._applyScale();
    },
    zoomOut() {
      if (this.SCALE <= 1) return;
      this.SCALE--;
      this._applyScale();
    },
    _applyScale() {
      const canvas = document.getElementById('led-canvas');
      if (!canvas) return;
      window.ComposerCanvas.updateCanvasSize(this.MATRIX_W, this.MATRIX_H, this.SCALE);
      canvas.style.width  = (this.MATRIX_W * this.SCALE) + 'px';
      canvas.style.height = (this.MATRIX_H * this.SCALE) + 'px';
      this.render();
    },
    setCustomSize() {
      const input = prompt(
        'Enter canvas size as WxH (e.g. 96×48, 192×64):',
        `${this.MATRIX_W}×${this.MATRIX_H}`
      );
      if (!input) return;
      const m = input.match(/(\d+)[×xX*,\s]+(\d+)/);
      if (!m) { this._setStatus('Invalid size — use WxH format', 'error'); return; }
      const w = Math.max(8, Math.min(512, parseInt(m[1])));
      const h = Math.max(8, Math.min(256, parseInt(m[2])));
      this.MATRIX_W = w;
      this.MATRIX_H = h;
      this.currentPreset = `${w}×${h}`;
      this.SCALE = w <= 64 ? 6 : w <= 128 ? 4 : 2;
      this._applyScale();
      this._setStatus(`Canvas set to ${w}×${h}`, 'info');
    },
    zoomFit() {
      // Fit canvas to center column: subtract left (~224px) + right (~288px) panels + margins
      const avW = Math.max(80, window.innerWidth - 560);
      // Subtract header (44) + toolbar (~40) + layers (~180) + status (28) + padding
      const avH = Math.max(40, window.innerHeight - 340);
      const fitW = Math.floor(avW / this.MATRIX_W);
      const fitH = Math.floor(avH / this.MATRIX_H);
      this.SCALE = Math.max(1, Math.min(8, Math.min(fitW, fitH)));
      this._applyScale();
    },

    onBgColorChange(hex) {
      this.metadata.bgColor = {
        r: parseInt(hex.slice(1, 3), 16),
        g: parseInt(hex.slice(3, 5), 16),
        b: parseInt(hex.slice(5, 7), 16),
      };
      this.render();
    },

    // ── Canvas events ─────────────────────────────────────────────────
    onCanvasMouseDown(event) {
      const { lx, ly } = this._canvasToLed(event);

      // Priority 1: resize handle on selected rectangle (skip if locked)
      if (this.selectedElement?.type === 'rectangle' && !this.selectedElement.locked) {
        const handle = window.ComposerCanvas.getResizeHandle(
          this.selectedElement, lx, ly, this.MATRIX_W, this.MATRIX_H
        );
        if (handle) {
          const el = this.selectedElement;
          this._resize = {
            active: true, elemId: el.id, handle,
            startMX: event.clientX, startMY: event.clientY,
            startW: el.width, startH: el.height,
            startX: el.x, startY: el.y,
          };
          return;
        }
      }

      // Priority 2: element hit test (reverse = top layer first)
      const hit = [...this.elements].reverse().find(
        el => window.ComposerCanvas.hitTest(el, lx, ly, this.MATRIX_W, this.MATRIX_H)
      );

      if (hit) {
        this.selectedId = hit.id;
        if (!hit.locked) {
          const stored = this._getStoredPos(hit);
          this._drag = {
            active: true, elemId: hit.id,
            startMX: event.clientX, startMY: event.clientY,
            startEX: stored.x, startEY: stored.y,
          };
        }
      } else {
        this.selectedId = null;
      }
      this.render();
    },

    onCanvasMouseMove(event) {
      const { lx, ly } = this._canvasToLed(event);
      this.hoverInfo = `${lx}, ${ly}`;

      // Resize
      if (this._resize.active) {
        const dx = Math.round((event.clientX - this._resize.startMX) / this.SCALE);
        const dy = Math.round((event.clientY - this._resize.startMY) / this.SCALE);
        const el = this.elements.find(e => e.id === this._resize.elemId);
        if (!el) return;
        const h = this._resize.handle;
        if (h.includes('e')) el.width  = Math.max(1, this._resize.startW + dx);
        if (h.includes('s')) el.height = Math.max(1, this._resize.startH + dy);
        if (h.includes('w')) {
          const nw = Math.max(1, this._resize.startW - dx);
          el.x = this._resize.startX + (this._resize.startW - nw);
          el.width = nw;
        }
        if (h.includes('n')) {
          const nh = Math.max(1, this._resize.startH - dy);
          el.y = this._resize.startY + (this._resize.startH - nh);
          el.height = nh;
        }
        this.isDirty = true;
        this.render({ showTooltip: true });

        // Cursor
        const canvas = document.getElementById('led-canvas');
        if (canvas) canvas.style.cursor = window.ComposerCanvas.getCursorForHandle(h);
        return;
      }

      // Move drag
      if (this._drag.active) {
        const dx = Math.round((event.clientX - this._drag.startMX) / this.SCALE);
        const dy = Math.round((event.clientY - this._drag.startMY) / this.SCALE);
        const el = this.elements.find(e => e.id === this._drag.elemId);
        if (!el) return;

        if (el.type === 'line') {
          const newX = Math.max(0, Math.min(this.MATRIX_W - 1, this._drag.startEX + dx));
          const newY = Math.max(0, Math.min(this.MATRIX_H - 1, this._drag.startEY + dy));
          const offX = newX - el.x0, offY = newY - el.y0;
          el.x0 = newX; el.y0 = newY;
          el.x1 = Math.max(0, Math.min(this.MATRIX_W - 1, el.x1 + offX));
          el.y1 = Math.max(0, Math.min(this.MATRIX_H - 1, el.y1 + offY));
          this._drag.startEX = el.x0; this._drag.startEY = el.y0;
          this._drag.startMX = event.clientX; this._drag.startMY = event.clientY;
        } else {
          // Store anchor-relative offset
          const newStored = {
            x: this._drag.startEX + dx,
            y: this._drag.startEY + dy,
          };
          this._setStoredPos(el, newStored);
        }
        this.isDirty = true;
        this.render({ showTooltip: true });
        return;
      }

      // Cursor hints when hovering
      const canvas = document.getElementById('led-canvas');
      if (canvas) {
        let cursor = 'crosshair';
        if (this.selectedElement?.type === 'rectangle') {
          const handle = window.ComposerCanvas.getResizeHandle(
            this.selectedElement, lx, ly, this.MATRIX_W, this.MATRIX_H
          );
          if (handle) cursor = window.ComposerCanvas.getCursorForHandle(handle);
        }
        if (cursor === 'crosshair') {
          const hit = [...this.elements].reverse().find(
            el => window.ComposerCanvas.hitTest(el, lx, ly, this.MATRIX_W, this.MATRIX_H)
          );
          if (hit) cursor = 'move';
        }
        canvas.style.cursor = cursor;
      }
    },

    onCanvasMouseUp() {
      if (this._resize.active || this._drag.active) {
        this._resize = { active: false };
        this._drag   = { active: false };
        this._snapshot();
      }
      const canvas = document.getElementById('led-canvas');
      if (canvas) canvas.style.cursor = 'crosshair';
    },

    onCanvasMouseLeave() {
      this.hoverInfo = '';
      this.onCanvasMouseUp();
    },

    _canvasToLed(event) {
      const rect = event.target.getBoundingClientRect();
      const lx = Math.max(0, Math.min(this.MATRIX_W - 1, Math.floor((event.clientX - rect.left) / this.SCALE)));
      const ly = Math.max(0, Math.min(this.MATRIX_H - 1, Math.floor((event.clientY - rect.top) / this.SCALE)));
      return { lx: this._snap(lx), ly: this._snap(ly) };
    },

    _snap(v) {
      if (!this.snapToGrid || this.snapSize < 2) return v;
      return Math.round(v / this.snapSize) * this.snapSize;
    },

    // Anchor-aware position storage: x/y stored as offset from anchor
    _getStoredPos(el) {
      return { x: el.x ?? el.x0 ?? 0, y: el.y ?? el.y0 ?? 0 };
    },

    _setStoredPos(el, { x, y }) {
      if (this.snapToGrid && this.snapSize > 0) {
        x = Math.round(x / this.snapSize) * this.snapSize;
        y = Math.round(y / this.snapSize) * this.snapSize;
      }
      const clampX = v => Math.max(-this.MATRIX_W, Math.min(this.MATRIX_W * 2, v));
      const clampY = v => Math.max(-this.MATRIX_H, Math.min(this.MATRIX_H * 2, v));
      if (el.type === 'line') {
        el.x0 = clampX(x); el.y0 = clampY(y);
      } else {
        el.x = clampX(x); el.y = clampY(y);
      }
    },

    // ── Alignment ─────────────────────────────────────────────────────
    _alignElement(el, axis, mode) {
      const bb = window.ComposerCanvas.getBoundingBox(el, this.MATRIX_W, this.MATRIX_H);
      if (!bb) return;
      if (axis === 'x') {
        const newX = mode === 'start'  ? 0
                   : mode === 'center' ? Math.round((this.MATRIX_W - bb.w) / 2)
                   :                    this.MATRIX_W - bb.w;
        // Clear x-anchor so stored x IS the absolute position
        if ('xAnchor' in el) el.xAnchor = null;
        el.x = Math.round(newX);
        if (el.type === 'line') el.x0 = Math.round(newX);
      } else {
        const newY = mode === 'start'  ? 0
                   : mode === 'center' ? Math.round((this.MATRIX_H - bb.h) / 2)
                   :                    this.MATRIX_H - bb.h;
        if ('yAnchor' in el) el.yAnchor = null;
        el.y = Math.round(newY);
        if (el.type === 'line') el.y0 = Math.round(newY);
      }
      this._snapshot();
      this.isDirty = true;
      this.render();
    },
    alignLeft()         { if (this.selectedElement) this._alignElement(this.selectedElement, 'x', 'start');  },
    alignHCenter()      { if (this.selectedElement) this._alignElement(this.selectedElement, 'x', 'center'); },
    alignRight()        { if (this.selectedElement) this._alignElement(this.selectedElement, 'x', 'end');    },
    alignTop()          { if (this.selectedElement) this._alignElement(this.selectedElement, 'y', 'start');  },
    alignVCenter()      { if (this.selectedElement) this._alignElement(this.selectedElement, 'y', 'center'); },
    alignBottom()       { if (this.selectedElement) this._alignElement(this.selectedElement, 'y', 'end');    },

    // ── Keyboard shortcuts ────────────────────────────────────────────
    _onKeyDown(e) {
      const tag = document.activeElement?.tagName;
      const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';

      // Ctrl/Cmd combos work everywhere
      if (e.ctrlKey || e.metaKey) {
        if (e.key === 'z' && !e.shiftKey) { e.preventDefault(); this.undo(); return; }
        if ((e.key === 'y') || (e.key === 'z' && e.shiftKey)) { e.preventDefault(); this.redo(); return; }
        if (e.key === 'd' && this.selectedId) { e.preventDefault(); this.duplicateElement(this.selectedId); return; }
        if (e.key === 'c' && this.selectedId && !e.shiftKey) { e.preventDefault(); this.copyElement(); return; }
        if (e.key === 'c' && this.selectedId && e.shiftKey)  { e.preventDefault(); this.copyStyle(); return; }
        if (e.key === 'v' && this._clipboard && !e.shiftKey) { e.preventDefault(); this.pasteElement(); return; }
        if (e.key === 'v' && this._styleClipboard && e.shiftKey) { e.preventDefault(); this.pasteStyle(); return; }
        if (e.key === 'a') { e.preventDefault(); if (this.elements.length) { this.selectedId = this.elements[0].id; this.render(); } return; }
      }

      // Tab cycles through elements regardless of input focus
      if (e.key === 'Tab' && this.elements.length) {
        e.preventDefault();
        const idx = this.elements.findIndex(el => el.id === this.selectedId);
        const next = e.shiftKey
          ? (idx <= 0 ? this.elements.length - 1 : idx - 1)
          : (idx >= this.elements.length - 1 ? 0 : idx + 1);
        this.selectedId = this.elements[next].id;
        this.render();
        return;
      }

      if (inInput) return;

      const dist = e.shiftKey ? 5 : (this.snapToGrid && this.snapSize >= 2 ? this.snapSize : 1);
      if (e.key === 'ArrowLeft')  { e.preventDefault(); this.nudge(-dist, 0); }
      if (e.key === 'ArrowRight') { e.preventDefault(); this.nudge(dist,  0); }
      if (e.key === 'ArrowUp')    { e.preventDefault(); this.nudge(0, -dist); }
      if (e.key === 'ArrowDown')  { e.preventDefault(); this.nudge(0,  dist); }
      if ((e.key === 'Delete' || e.key === 'Backspace') && this.selectedId) {
        e.preventDefault();
        this.removeElement(this.selectedId);
      }
      if (e.key === 'Escape') {
        if (this.showHelpModal) { this.showHelpModal = false; return; }
        this.selectedId = null; this.render();
      }
      if (e.key === '?') { this.showHelpModal = !this.showHelpModal; }
      if (e.key === 'g' || e.key === 'G') { this.snapToGrid = !this.snapToGrid; }
      if (e.key === ']' && this.selectedId) { this.bringToFront(this.selectedId); }
      if (e.key === '[' && this.selectedId) { this.sendToBack(this.selectedId); }
    },

    nudge(dx, dy) {
      const el = this.selectedElement;
      if (!el) return;
      if (el.locked) { this._setStatus('Unlock element to move it', 'info'); return; }
      if (el.type === 'line') {
        el.x0 = Math.max(0, Math.min(this.MATRIX_W - 1, el.x0 + dx));
        el.y0 = Math.max(0, Math.min(this.MATRIX_H - 1, el.y0 + dy));
        el.x1 = Math.max(0, Math.min(this.MATRIX_W - 1, el.x1 + dx));
        el.y1 = Math.max(0, Math.min(this.MATRIX_H - 1, el.y1 + dy));
      } else if (el.type === 'divider') {
        if (el.orientation === 'horizontal') el.y = Math.max(0, Math.min(this.MATRIX_H - 1, (el.y ?? 0) + dy));
        else el.x = Math.max(0, Math.min(this.MATRIX_W - 1, (el.x ?? 0) + dx));
      } else {
        el.x = (el.x ?? 0) + dx;
        el.y = (el.y ?? 0) + dy;
      }
      this.isDirty = true;
      this._snapshot();
      this.render();
    },

    // ── Element management ────────────────────────────────────────────
    addElement(type) {
      const defaults = window.ComposerCanvas.ELEMENT_DEFAULTS[type];
      if (!defaults) return;
      const el = {
        id: this._nextId++,
        type,
        label: '',
        conditions: [],
        xAnchor: null, yAnchor: null, minWidth: 0,
        x: Math.floor(this.MATRIX_W / 4),
        y: Math.floor(this.MATRIX_H / 2) - 4,
        ...structuredClone(defaults),
      };
      if (type === 'line') {
        const cx = Math.floor(this.MATRIX_W / 4), cy = Math.floor(this.MATRIX_H / 2);
        el.x0 = cx; el.y0 = cy;
        el.x1 = Math.min(this.MATRIX_W - 1, cx + 30); el.y1 = cy;
        el.x = cx; el.y = cy;
      }
      this.elements.push(el);
      this.selectedId = el.id;
      this.isDirty = true;
      this._snapshot();
      this.render();
    },

    duplicateElement(id) {
      const el = this.elements.find(e => e.id === id);
      if (!el) return;
      const copy = {
        ...structuredClone(el),
        id: this._nextId++,
        label: el.label ? el.label + ' copy' : '',
      };
      // Offset slightly so it's visually distinct
      if (copy.type === 'line') { copy.x0 += 4; copy.y0 += 4; copy.x1 += 4; copy.y1 += 4; }
      else { copy.x = (copy.x ?? 0) + 4; copy.y = (copy.y ?? 0) + 4; }

      const idx = this.elements.findIndex(e => e.id === id);
      this.elements.splice(idx + 1, 0, copy);
      this.selectedId = copy.id;
      this.isDirty = true;
      this._snapshot();
      this.render();
    },

    copyElement() {
      if (!this.selectedElement) return;
      this._clipboard = structuredClone(this.selectedElement);
      this._setStatus(`Copied: ${this.selectedElement.label || this.selectedElement.type}`, 'info');
    },

    copyStyle() {
      const el = this.selectedElement;
      if (!el) return;
      this._styleClipboard = { r: el.r, g: el.g, b: el.b, font: el.font };
      this._setStatus('Style copied — select another element and paste style', 'info');
    },

    pasteStyle() {
      const el = this.selectedElement;
      if (!el || !this._styleClipboard) return;
      const { r, g, b, font } = this._styleClipboard;
      if (r !== undefined) { el.r = r; el.g = g; el.b = b; }
      if (font !== undefined && el.font !== undefined) el.font = font;
      this.isDirty = true;
      this._snapshot();
      this.render();
      this._setStatus('Style pasted', 'success');
    },

    pasteElement() {
      if (!this._clipboard) return;
      const copy = structuredClone(this._clipboard);
      copy.id = this._nextId++;
      if (copy.type === 'line') { copy.x0 += 6; copy.y0 += 6; copy.x1 += 6; copy.y1 += 6; }
      else { copy.x = (copy.x ?? 0) + 6; copy.y = (copy.y ?? 0) + 6; }
      this.elements.push(copy);
      this.selectedId = copy.id;
      this.isDirty = true;
      this._snapshot();
      this.render();
      this._setStatus('Pasted element', 'success');
    },

    removeElement(id) {
      this.elements = this.elements.filter(e => e.id !== id);
      if (this.selectedId === id) this.selectedId = null;
      this.isDirty = true;
      this._snapshot();
      this.render();
    },

    moveElementUp(id) {
      const idx = this.elements.findIndex(e => e.id === id);
      if (idx < this.elements.length - 1) {
        [this.elements[idx], this.elements[idx + 1]] = [this.elements[idx + 1], this.elements[idx]];
        this.isDirty = true;
        this._snapshot();
        this.render();
      }
    },

    moveElementDown(id) {
      const idx = this.elements.findIndex(e => e.id === id);
      if (idx > 0) {
        [this.elements[idx], this.elements[idx - 1]] = [this.elements[idx - 1], this.elements[idx]];
        this.isDirty = true;
        this._snapshot();
        this.render();
      }
    },

    bringToFront(id) {
      const idx = this.elements.findIndex(e => e.id === id);
      if (idx < this.elements.length - 1) {
        this.elements.push(this.elements.splice(idx, 1)[0]);
        this.isDirty = true;
        this._snapshot();
        this.render();
      }
    },

    sendToBack(id) {
      const idx = this.elements.findIndex(e => e.id === id);
      if (idx > 0) {
        this.elements.unshift(this.elements.splice(idx, 1)[0]);
        this.isDirty = true;
        this._snapshot();
        this.render();
      }
    },

    clearAll() {
      if (this.elements.length === 0) return;
      if (!confirm('Remove all elements from the canvas?')) return;
      this.elements = [];
      this.selectedId = null;
      this.isDirty = true;
      this._snapshot();
      this.render();
    },

    // ── Alignment ─────────────────────────────────────────────────────
    alignElement(dir) {
      const el = this.selectedElement;
      if (!el) return;
      const bb = window.ComposerCanvas.getBoundingBox(el, this.MATRIX_W, this.MATRIX_H);
      switch (dir) {
        case 'left':   el.x = 0; break;
        case 'center': el.x = Math.round((this.MATRIX_W - bb.w) / 2); break;
        case 'right':  el.x = this.MATRIX_W - bb.w; break;
        case 'top':    el.y = 0; break;
        case 'middle': el.y = Math.round((this.MATRIX_H - bb.h) / 2); break;
        case 'bottom': el.y = this.MATRIX_H - bb.h; break;
      }
      this.isDirty = true;
      this._snapshot();
      this.render();
    },

    // ── Templates ─────────────────────────────────────────────────────
    loadTemplate(tmpl) {
      if (this.isDirty && this.elements.length > 0) {
        if (!confirm('Load template? Your current canvas will be replaced.')) return;
      }
      this.elements = tmpl.elements.map((el, i) => ({
        xAnchor: null, yAnchor: null, minWidth: 0, conditions: [],
        ...structuredClone(el),
        id: i + 1,
      }));
      this._nextId = this.elements.length + 1;
      this.dataModel = structuredClone(tmpl.dataModel);
      if (tmpl.preset) this.changePreset(tmpl.preset, { silent: true });
      this.selectedId = null;
      this.showTemplateModal = false;
      this.isDirty = true;
      this._snapshot();
      this.render();
      this._setStatus(`Loaded template: ${tmpl.label}`, 'success');
    },

    // ── Metadata helpers ──────────────────────────────────────────────
    onNameInput() {
      if (this._idAutoGenerated) {
        this.metadata.id = this.metadata.name
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, '-')
          .replace(/^-+|-+$/g, '');
      }
    },
    onIdInput() { this._idAutoGenerated = false; },
    isIdValid() { return /^[a-z][a-z0-9-]{0,62}$/.test(this.metadata.id); },

    // ── Config variables ──────────────────────────────────────────────
    addConfigVar() {
      const key = this.newConfigVar.key.trim();
      if (!key) { this._setStatus('Key is required', 'error'); return; }
      if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(key)) {
        this._setStatus('Key must be a valid Python identifier', 'error'); return;
      }
      if (this.dataModel.configVars.find(v => v.key === key)) {
        this._setStatus(`"${key}" already exists`, 'error'); return;
      }
      this.dataModel.configVars.push({ ...this.newConfigVar, key });
      this.newConfigVar = { key:'', label:'', type:'string', default:'', description:'' };
      this.showConfigVarModal = false;
      this.isDirty = true;
      this._snapshot();
    },

    removeConfigVar(key) {
      const bound = this.elements.filter(
        e => e.type === 'dynamic_text' && e.binding?.source === 'config' && e.binding?.key === key
      );
      if (bound.length && !confirm(`"${key}" is used by ${bound.length} element(s). Remove anyway?`)) return;
      this.dataModel.configVars = this.dataModel.configVars.filter(v => v.key !== key);
      this.isDirty = true;
      this._snapshot();
    },

    _isBound(key) {
      return this.elements.some(
        e => e.type === 'dynamic_text' && e.binding?.source === 'config' && e.binding?.key === key
      );
    },

    addPreMadeVar(varDef) {
      if (this.dataModel.configVars.find(v => v.key === varDef.key)) {
        this._setStatus(`"${varDef.key}" already added`, 'warning'); return;
      }
      this.dataModel.configVars.push({ ...varDef });
      this.isDirty = true;
      this._snapshot();
      this._setStatus(`Added: ${varDef.key}`, 'success');
    },

    // ── Open existing plugin ──────────────────────────────────────────
    async openPluginModal() {
      this.showOpenModal = true;
      this.installedPlugins = [];
      this.loadPluginsStatus = 'loading';
      try {
        const resp = await fetch('/composer/api/plugins');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        this.installedPlugins = await resp.json();
        this.loadPluginsStatus = 'done';
      } catch (e) {
        this.loadPluginsStatus = 'error';
        this._setStatus('Could not load plugin list: ' + e.message, 'error');
      }
    },

    async loadPlugin(pluginId) {
      this.showOpenModal = false;
      try {
        const resp = await fetch(`/composer/api/load/${pluginId}`);
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          throw new Error(err.message || `HTTP ${resp.status}`);
        }
        const data = await resp.json();
        const state = data.state ?? data;
        this._applyState(state);
        this._idAutoGenerated = false;
        this.isDirty = false;
        this._snapshot();
        this._setStatus(`Loaded: ${pluginId}` + (data.source === 'schema_import' ? ' (config vars only)' : ''), 'success');
        this.render();
      } catch (e) {
        this._setStatus('Failed to load plugin: ' + e.message, 'error');
      }
    },

    // ── Code preview ──────────────────────────────────────────────────
    async showPreview() {
      if (!this._validateBeforeExport()) return;
      this.showCodeModal = true;
      this.loadingCode = true;
      this.codeFiles = {};
      try {
        const resp = await fetch('/composer/api/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this._buildPayload()),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.message || `HTTP ${resp.status}`);
        this.codeFiles = data.files;
        this.codeTab = 'manager.py';
      } catch (e) {
        this._setStatus('Preview failed: ' + e.message, 'error');
        this.showCodeModal = false;
      } finally {
        this.loadingCode = false;
      }
    },

    // ── Color helpers ─────────────────────────────────────────────────
    rgbToHex(r, g, b) {
      return '#' + [r, g, b].map(v => Math.max(0, Math.min(255, v)).toString(16).padStart(2, '0')).join('');
    },

    onColorChange(el, hexField, hex) {
      const r = parseInt(hex.slice(1, 3), 16);
      const g = parseInt(hex.slice(3, 5), 16);
      const b = parseInt(hex.slice(5, 7), 16);
      if (hexField === 'fill')      { el.fillR  = r; el.fillG  = g; el.fillB  = b; }
      else if (hexField === 'out') { el.outR   = r; el.outG   = g; el.outB   = b; }
      else if (hexField === 'bg')  { el.bgR    = r; el.bgG    = g; el.bgB    = b; }
      else if (hexField === 'track') { el.trackR = r; el.trackG = g; el.trackB = b; }
      else if (hexField === 'empty') { el.emptyR = r; el.emptyG = g; el.emptyB = b; }
      else                         { el.r = r; el.g = g; el.b = b; }
      this._trackColor(r, g, b);
      this.render();
    },

    applyPaletteColor(el, hexField, swatch) {
      const { r, g, b } = swatch;
      if (hexField === 'fill')      { el.fillR  = r; el.fillG  = g; el.fillB  = b; }
      else if (hexField === 'out') { el.outR   = r; el.outG   = g; el.outB   = b; }
      else if (hexField === 'bg')  { el.bgR    = r; el.bgG    = g; el.bgB    = b; }
      else if (hexField === 'track') { el.trackR = r; el.trackG = g; el.trackB = b; }
      else if (hexField === 'empty') { el.emptyR = r; el.emptyG = g; el.emptyB = b; }
      else                         { el.r = r; el.g = g; el.b = b; }
      this._trackColor(r, g, b);
      this.isDirty = true;
      this._snapshot();
      this.render();
    },

    // ── Export & Install ──────────────────────────────────────────────
    _buildPayload() {
      return {
        composer_version: '1.0',
        metadata: this.metadata,
        elements: this.elements,
        dataModel: this.dataModel,
        preset: this.currentPreset,
      };
    },

    _validateBeforeExport() {
      if (!this.metadata.name.trim())   { this._setStatus('Plugin name is required', 'error'); return false; }
      if (!this.isIdValid())            { this._setStatus('Plugin ID is invalid', 'error'); return false; }
      if (!this.metadata.author.trim()) { this._setStatus('Author is required', 'error'); return false; }
      if (this.elements.length === 0)   { this._setStatus('Add at least one element', 'error'); return false; }
      const unbound = this.elements.filter(
        e => (e.type === 'dynamic_text' || e.type === 'progress_bar') && !e.binding?.key
      );
      if (unbound.length) { this._setStatus(`${unbound.length} element(s) have no variable bound`, 'error'); return false; }
      return true;
    },

    async generateZip() {
      if (!this._validateBeforeExport()) return;
      this.generateStatus = 'working';
      try {
        const resp = await fetch('/composer/api/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this._buildPayload()),
        });
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({ message: 'Server error' }));
          throw new Error(err.message || `HTTP ${resp.status}`);
        }
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = `${this.metadata.id}.zip`; a.click();
        URL.revokeObjectURL(url);
        this.generateStatus = 'done';
        this._setStatus('Plugin ZIP downloaded', 'success');
        setTimeout(() => { if (this.generateStatus === 'done') this.generateStatus = 'idle'; }, 4000);
      } catch (err) {
        this.generateStatus = 'error';
        this._setStatus(err.message, 'error');
        setTimeout(() => { if (this.generateStatus === 'error') this.generateStatus = 'idle'; }, 6000);
      }
    },

    async installLocally(force = false) {
      if (!this._validateBeforeExport()) return;
      this.installStatus = 'working';
      try {
        const resp = await fetch('/composer/api/install', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...this._buildPayload(), _force: force }),
        });
        const data = await resp.json().catch(() => ({ message: 'Server error' }));

        if (resp.status === 409 && !force) {
          this.installStatus = 'idle';
          if (confirm(`Plugin "${this.metadata.id}" is already installed.\n\nOverwrite it with the current design?`)) {
            return this.installLocally(true);
          }
          return;
        }

        if (!resp.ok) throw new Error(data.message || `HTTP ${resp.status}`);

        this.installStatus = 'done';
        this.isDirty = false;
        localStorage.removeItem(LS_KEY);
        this._setStatus(`"${this.metadata.id}" installed — enable it in the Plugin Manager`, 'success');
        setTimeout(() => { if (this.installStatus === 'done') this.installStatus = 'idle'; }, 6000);
      } catch (err) {
        this.installStatus = 'error';
        this._setStatus(err.message, 'error');
        setTimeout(() => { if (this.installStatus === 'error') this.installStatus = 'idle'; }, 6000);
      }
    },

    // ── Color themes ──────────────────────────────────────────────────
    applyColorTheme(theme) {
      // Distribute theme colors round-robin across text-type elements
      const textTypes = ['text', 'dynamic_text', 'clock', 'countdown', 'marquee', 'gauge'];
      let idx = 0;
      for (const el of this.elements) {
        if (textTypes.includes(el.type)) {
          const c = theme.colors[idx % theme.colors.length];
          el.r = c.r; el.g = c.g; el.b = c.b;
          idx++;
        }
      }
      this.isDirty = true;
      this._snapshot();
      this.render();
      this._setStatus(`Applied theme: ${theme.label}`, 'success');
    },

    // ── Status ────────────────────────────────────────────────────────
    _setStatus(msg, type = 'info') {
      this.statusMsg = msg;
      this.statusType = type;
    },

    statusClass() {
      return {
        info:    'text-gray-500',
        success: 'text-green-600',
        error:   'text-red-600',
        warning: 'text-yellow-600',
      }[this.statusType] || 'text-gray-500';
    },
  };
}
