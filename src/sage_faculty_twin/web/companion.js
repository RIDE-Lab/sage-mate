(() => {
    "use strict";

    const STORAGE_KEY = "sageMateCompanion:v2";
    const LEGACY_STORAGE_KEY = "sageMateCompanion:v1";
    const DEFAULT_PROFILE = Object.freeze({
        version: 2,
        name: "小 Sage",
        appearance: "aurora",
        temperament: "calm",
        soundEnabled: false,
        hidden: false,
        bond: 20,
    });
    const VALID_STATES = new Set(["idle", "thinking", "happy", "worried"]);
    const VALID_APPEARANCES = new Set(["aurora", "mint", "sunset"]);
    const VALID_TEMPERAMENTS = new Set(["calm", "curious", "lively"]);
    const IDLE_MESSAGES = Object.freeze({
        calm: "我在这里陪你，随时可以开始一个新问题。",
        curious: "今天想一起弄明白什么？我已经准备好探索了。",
        lively: "来吧！把最想解决的问题交给我，我们一起出发。",
    });
    const GROWTH_STAGES = Object.freeze([
        Object.freeze({ minimum: 0, name: "初识" }),
        Object.freeze({ minimum: 30, name: "熟悉伙伴" }),
        Object.freeze({ minimum: 60, name: "默契搭档" }),
        Object.freeze({ minimum: 85, name: "灵感知己" }),
    ]);

    class SageCompanionController {
        constructor({ chatForm, chatShell, chatQuestion }) {
            this.root = document.getElementById("sage-companion");
            this.toggle = document.getElementById("sage-companion-toggle");
            this.panel = document.getElementById("sage-companion-panel");
            this.closeButton = document.getElementById("sage-companion-close");
            this.message = document.getElementById("sage-companion-message");
            this.nameLabel = document.getElementById("sage-companion-name");
            this.nameInput = document.getElementById("sage-companion-name-input");
            this.stageLabel = document.getElementById("sage-companion-stage");
            this.stageHint = document.getElementById("sage-companion-stage-hint");
            this.bondValue = document.getElementById("sage-companion-bond-value");
            this.bondBar = document.getElementById("sage-companion-bond-bar");
            this.temperamentSelect = document.getElementById("sage-companion-temperament");
            this.soundToggle = document.getElementById("sage-companion-sound");
            this.settingsTrigger = document.getElementById("open-companion-settings");
            this.chatForm = chatForm;
            this.chatShell = chatShell;
            this.chatQuestion = chatQuestion;
            this.profile = { ...DEFAULT_PROFILE };
            this.requestActive = false;
            this.resetTimer = null;
            this.resizeObserver = null;
            this.classObserver = null;
            this.initialized = false;
        }

        clampBond(value) {
            const parsed = Number(value);
            if (!Number.isFinite(parsed)) {
                return DEFAULT_PROFILE.bond;
            }
            return Math.min(100, Math.max(0, Math.round(parsed)));
        }

        sanitizeName(value) {
            const clean = String(value || "")
                .replace(/[\u0000-\u001f\u007f]/g, "")
                .replace(/\s+/g, " ")
                .trim();
            return Array.from(clean).slice(0, 12).join("") || DEFAULT_PROFILE.name;
        }

        normalizeProfile(stored = {}) {
            return {
                version: DEFAULT_PROFILE.version,
                name: this.sanitizeName(stored.name),
                appearance: VALID_APPEARANCES.has(stored.appearance)
                    ? stored.appearance
                    : DEFAULT_PROFILE.appearance,
                temperament: VALID_TEMPERAMENTS.has(stored.temperament)
                    ? stored.temperament
                    : DEFAULT_PROFILE.temperament,
                soundEnabled: stored.soundEnabled === true,
                hidden: stored.hidden === true,
                bond: this.clampBond(stored.bond),
            };
        }

        load() {
            try {
                const current = localStorage.getItem(STORAGE_KEY);
                const legacy = localStorage.getItem(LEGACY_STORAGE_KEY);
                this.profile = this.normalizeProfile(JSON.parse(current || legacy || "{}"));
                if (!current && legacy) {
                    this.persist();
                }
            } catch {
                this.profile = { ...DEFAULT_PROFILE };
            }
        }

        persist() {
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify(this.profile));
            } catch {
                // Keep the companion usable when browser storage is unavailable.
            }
        }

        idleMessage() {
            return IDLE_MESSAGES[this.profile.temperament] || IDLE_MESSAGES.calm;
        }

        growthStage() {
            return GROWTH_STAGES.reduce(
                (current, stage) => this.profile.bond >= stage.minimum ? stage : current,
                GROWTH_STAGES[0]
            );
        }

        renderBond() {
            const stage = this.growthStage();
            const stageIndex = GROWTH_STAGES.indexOf(stage);
            const nextStage = GROWTH_STAGES[stageIndex + 1];
            if (this.bondValue) {
                this.bondValue.textContent = `${this.profile.bond}%`;
            }
            if (this.bondBar) {
                this.bondBar.style.width = `${this.profile.bond}%`;
            }
            if (this.stageLabel) {
                this.stageLabel.textContent = stage.name;
            }
            if (this.stageHint) {
                this.stageHint.textContent = nextStage
                    ? `再积累 ${nextStage.minimum - this.profile.bond}% 默契，就会成为${nextStage.name}。`
                    : "默契已经满格，继续一起探索吧。";
            }
        }

        renderProfile() {
            if (!this.root) {
                return;
            }
            this.root.dataset.appearance = this.profile.appearance;
            this.root.dataset.temperament = this.profile.temperament;
            this.root.hidden = this.profile.hidden;
            if (this.nameLabel) {
                this.nameLabel.textContent = this.profile.name;
            }
            if (this.nameInput) {
                this.nameInput.value = this.profile.name;
            }
            this.panel?.querySelectorAll('input[name="sage_companion_appearance"]').forEach((input) => {
                input.checked = input.value === this.profile.appearance;
            });
            if (this.temperamentSelect) {
                this.temperamentSelect.value = this.profile.temperament;
            }
            if (this.soundToggle) {
                this.soundToggle.checked = this.profile.soundEnabled;
            }
            if (this.settingsTrigger) {
                this.settingsTrigger.setAttribute(
                    "aria-label",
                    this.profile.hidden ? `恢复电子伙伴${this.profile.name}` : `打开电子伙伴${this.profile.name}`
                );
                this.settingsTrigger.title = this.profile.hidden ? "恢复电子伙伴" : "电子伙伴";
            }
            this.renderBond();
            this.syncToggleLabel();
        }

        syncToggleLabel() {
            if (!this.toggle) {
                return;
            }
            const message = this.message?.textContent?.trim() || this.idleMessage();
            const action = this.toggle.getAttribute("aria-expanded") === "true" ? "关闭" : "打开";
            this.toggle.setAttribute(
                "aria-label",
                `${this.profile.name}，当前状态：${message} 点击${action}伙伴面板`
            );
        }

        playTone(kind) {
            if (!this.profile.soundEnabled || this.profile.hidden) {
                return;
            }
            const AudioContext = globalThis.AudioContext || globalThis.webkitAudioContext;
            if (typeof AudioContext !== "function") {
                return;
            }
            try {
                const context = new AudioContext();
                const oscillator = context.createOscillator();
                const gain = context.createGain();
                const frequencies = { thinking: 392, happy: 659, worried: 262 };
                oscillator.frequency.value = frequencies[kind] || 523;
                oscillator.type = "sine";
                gain.gain.setValueAtTime(0.0001, context.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.055, context.currentTime + 0.015);
                gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.16);
                oscillator.connect(gain);
                gain.connect(context.destination);
                oscillator.start();
                oscillator.stop(context.currentTime + 0.17);
                oscillator.addEventListener("ended", () => context.close());
            } catch {
                // Audio is optional; browser policy must never block companion interaction.
            }
        }

        setRequestActive(active) {
            this.requestActive = Boolean(active);
        }

        setState(state, message, { resetAfterMs = 0 } = {}) {
            if (!this.root) {
                return;
            }
            if (this.resetTimer) {
                globalThis.clearTimeout(this.resetTimer);
                this.resetTimer = null;
            }
            const normalizedState = VALID_STATES.has(state) ? state : "idle";
            const normalizedMessage = String(message || this.idleMessage()).trim();
            const stateChanged = this.root.dataset.state !== normalizedState;
            this.root.dataset.state = normalizedState;
            if (this.message) {
                this.message.textContent = normalizedMessage;
            }
            this.syncToggleLabel();
            if (stateChanged && normalizedState !== "idle") {
                this.playTone(normalizedState);
            }
            if (resetAfterMs > 0) {
                this.resetTimer = globalThis.setTimeout(() => {
                    this.resetTimer = null;
                    if (!this.requestActive) {
                        this.setState("idle", this.idleMessage());
                    }
                }, resetAfterMs);
            }
        }

        setPanelOpen(open, { returnFocus = true } = {}) {
            if (!this.panel || !this.toggle || this.profile.hidden) {
                return;
            }
            const nextOpen = Boolean(open);
            this.panel.hidden = !nextOpen;
            this.toggle.setAttribute("aria-expanded", String(nextOpen));
            this.syncToggleLabel();
            if (nextOpen) {
                this.closeButton?.focus();
                globalThis.requestAnimationFrame(() => this.syncPlacement());
            } else if (returnFocus) {
                this.toggle.focus();
            }
        }

        adjustBond(increment) {
            const previousStage = this.growthStage();
            this.profile.bond = this.clampBond(this.profile.bond + increment);
            const nextStage = this.growthStage();
            this.renderBond();
            this.persist();
            return previousStage.name !== nextStage.name ? nextStage : null;
        }

        saveName() {
            this.profile.name = this.sanitizeName(this.nameInput?.value);
            this.persist();
            this.renderProfile();
            this.setState("happy", `以后就叫我${this.profile.name}吧，很高兴正式认识你！`, { resetAfterMs: 2400 });
        }

        hide() {
            this.setPanelOpen(false, { returnFocus: false });
            this.profile.hidden = true;
            this.persist();
            this.renderProfile();
            this.settingsTrigger?.focus();
        }

        restore() {
            this.profile.hidden = false;
            this.persist();
            this.renderProfile();
            this.setState("happy", `${this.profile.name}回来啦！我们继续一起探索吧。`, { resetAfterMs: 2200 });
            this.setPanelOpen(true);
        }

        handleAction(action) {
            const normalizedAction = String(action || "");
            let milestone = null;
            if (normalizedAction === "pet") {
                milestone = this.adjustBond(4);
                this.setState("happy", "呼噜呼噜，谢谢你！我会继续认真陪你。", { resetAfterMs: 2200 });
            } else if (normalizedAction === "inspire") {
                milestone = this.adjustBond(7);
                this.setState("happy", "灵感充满啦！试着把现在最想解决的问题告诉我吧。", { resetAfterMs: 2600 });
                this.chatQuestion?.focus();
            } else if (normalizedAction === "save-name") {
                this.saveName();
            } else if (normalizedAction === "hide") {
                this.hide();
            } else {
                return;
            }
            if (milestone) {
                this.setState(
                    "happy",
                    `新的成长阶段：${milestone.name}！我们的默契又向前了一步。`,
                    { resetAfterMs: 3200 }
                );
            }
        }

        syncPlacement() {
            if (!this.root || !this.chatForm || !this.chatShell || this.profile.hidden) {
                return;
            }
            const formRect = this.chatForm.getBoundingClientRect();
            const shellRect = this.chatShell.getBoundingClientRect();
            const toggleRect = this.toggle?.getBoundingClientRect();
            const usesViewportPosition = globalThis.matchMedia?.("(max-width: 720px)").matches;
            const layoutBottom = usesViewportPosition ? globalThis.innerHeight : shellRect.bottom;
            const layoutTop = usesViewportPosition ? 0 : shellRect.top;
            const bottomOffset = Math.max(12, Math.ceil(layoutBottom - formRect.top + 12));
            const panelMaxHeight = Math.max(
                160,
                Math.floor(formRect.top - layoutTop - (toggleRect?.height || 64) - 24)
            );
            this.root.style.setProperty("--sage-companion-bottom-offset", `${bottomOffset}px`);
            this.root.style.setProperty("--sage-companion-panel-max-height", `${panelMaxHeight}px`);
        }

        bindSettings() {
            this.panel?.addEventListener("change", (event) => {
                const target = event.target;
                if (target.matches('input[name="sage_companion_appearance"]')) {
                    this.profile.appearance = VALID_APPEARANCES.has(target.value)
                        ? target.value
                        : DEFAULT_PROFILE.appearance;
                    this.persist();
                    this.renderProfile();
                } else if (target === this.temperamentSelect) {
                    this.profile.temperament = VALID_TEMPERAMENTS.has(target.value)
                        ? target.value
                        : DEFAULT_PROFILE.temperament;
                    this.persist();
                    this.renderProfile();
                    if (!this.requestActive) {
                        this.setState("idle", this.idleMessage());
                    }
                } else if (target === this.soundToggle) {
                    this.profile.soundEnabled = target.checked;
                    this.persist();
                    if (this.profile.soundEnabled) {
                        this.playTone("happy");
                    }
                }
            });
            this.nameInput?.addEventListener("keydown", (event) => {
                if (event.key === "Enter") {
                    event.preventDefault();
                    this.saveName();
                }
            });
            this.panel?.querySelector(".sage-companion-settings")?.addEventListener("toggle", () => {
                globalThis.requestAnimationFrame(() => this.syncPlacement());
            });
        }

        init() {
            if (this.initialized || !this.root || !this.toggle || !this.panel) {
                return;
            }
            this.initialized = true;
            this.load();
            this.renderProfile();
            this.setState("idle", this.idleMessage());
            this.syncPlacement();
            globalThis.addEventListener("resize", () => this.syncPlacement());
            if (typeof ResizeObserver === "function") {
                this.resizeObserver = new ResizeObserver(() => this.syncPlacement());
                this.resizeObserver.observe(this.chatForm);
            }
            if (typeof MutationObserver === "function") {
                this.classObserver = new MutationObserver(() => {
                    globalThis.requestAnimationFrame(() => this.syncPlacement());
                });
                this.classObserver.observe(document.body, { attributes: true, attributeFilter: ["class"] });
                this.classObserver.observe(this.chatShell, { attributes: true, attributeFilter: ["class"] });
            }
            this.toggle.addEventListener("click", () => this.setPanelOpen(this.panel.hidden));
            this.closeButton?.addEventListener("click", () => this.setPanelOpen(false));
            this.settingsTrigger?.addEventListener("click", () => {
                if (this.profile.hidden) {
                    this.restore();
                } else {
                    this.setPanelOpen(true);
                }
            });
            this.panel.addEventListener("click", (event) => {
                const actionButton = event.target.closest("[data-companion-action]");
                if (actionButton) {
                    this.handleAction(actionButton.dataset.companionAction);
                }
            });
            this.bindSettings();
            document.addEventListener("keydown", (event) => {
                if (event.key === "Escape" && !this.panel.hidden) {
                    this.setPanelOpen(false);
                }
            });
            globalThis.requestAnimationFrame(() => this.syncPlacement());
        }
    }

    globalThis.SageCompanion = Object.freeze({
        create(options) {
            return new SageCompanionController(options);
        },
    });
})();
