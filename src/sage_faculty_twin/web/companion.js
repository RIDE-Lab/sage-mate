(() => {
    "use strict";

    const STORAGE_KEY = "sageMateCompanion:v3";
    const LEGACY_STORAGE_KEYS = Object.freeze(["sageMateCompanion:v2", "sageMateCompanion:v1"]);
    const DEFAULT_PROFILE = Object.freeze({
        version: 3,
        name: "小 Sage",
        appearance: "aurora",
        temperament: "calm",
        soundEnabled: false,
        hidden: false,
        bond: 20,
        answersCompleted: 0,
        streakDays: 0,
        lastAnswerDate: "",
        dailyQuestDate: "",
        dailyQuestIndex: 0,
        dailyQuestCompleted: false,
        interactionCount: 0,
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
        Object.freeze({ minimum: 0, key: "newcomer", name: "初识" }),
        Object.freeze({ minimum: 30, key: "familiar", name: "熟悉伙伴" }),
        Object.freeze({ minimum: 60, key: "partner", name: "默契搭档" }),
        Object.freeze({ minimum: 85, key: "confidant", name: "灵感知己" }),
    ]);
    const DAILY_QUESTS = Object.freeze([
        "把一个复杂问题改写成一句更清晰的问题。",
        "追问一次：这个结论的边界条件是什么？",
        "请伙伴给出一个反例，检查理解是否稳固。",
        "用三句话总结刚刚学到的内容。",
        "把今天的问题拆成“已知、未知、下一步”。",
        "尝试向完全不了解这个主题的人解释一次。",
    ]);
    const REACTIONS = Object.freeze({
        calm: Object.freeze({
            pet: Object.freeze(["呼噜……收到你的鼓励了。", "这样安静地陪着也很好。", "我会稳稳地陪你想清楚。"]),
            inspire: Object.freeze(["灵感豆收好啦，我们慢慢把问题拆开。", "补充完能量，下一步从最关键的问题开始。"]),
        }),
        curious: Object.freeze({
            pet: Object.freeze(["嘿，我又想到一个可以追问的角度！", "摸摸收到，要不要一起找个反例？", "好奇心正在升温。"]),
            inspire: Object.freeze(["灵感豆启动！今天想探索哪个未知？", "能量满格，我想看看问题的另一面。"]),
        }),
        lively: Object.freeze({
            pet: Object.freeze(["击掌！今天也一起向前冲。", "好耶，陪伴能量加满！", "转个圈！下一个问题交给我。"]),
            inspire: Object.freeze(["灵感豆爆发！马上开启新挑战。", "叮——新的点子已经上线！"]),
        }),
    });

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
            this.answersValue = document.getElementById("sage-companion-answers");
            this.streakValue = document.getElementById("sage-companion-streak");
            this.questText = document.getElementById("sage-companion-quest-text");
            this.questStatus = document.getElementById("sage-companion-quest-status");
            this.questCompleteButton = this.panel?.querySelector('[data-companion-action="complete-quest"]');
            this.temperamentSelect = document.getElementById("sage-companion-temperament");
            this.soundToggle = document.getElementById("sage-companion-sound");
            this.settingsTrigger = document.getElementById("open-companion-settings");
            this.chatForm = chatForm;
            this.chatShell = chatShell;
            this.chatQuestion = chatQuestion;
            this.profile = { ...DEFAULT_PROFILE };
            this.requestActive = false;
            this.completionRecordedForActiveRequest = false;
            this.resetTimer = null;
            this.celebrationTimer = null;
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

        boundedCount(value, maximum = 9999) {
            const parsed = Number(value);
            if (!Number.isFinite(parsed)) {
                return 0;
            }
            return Math.min(maximum, Math.max(0, Math.round(parsed)));
        }

        normalizeDate(value) {
            const normalized = String(value || "");
            return /^\d{4}-\d{2}-\d{2}$/.test(normalized) ? normalized : "";
        }

        todayKey() {
            const today = new Date();
            const year = today.getFullYear();
            const month = String(today.getMonth() + 1).padStart(2, "0");
            const day = String(today.getDate()).padStart(2, "0");
            return `${year}-${month}-${day}`;
        }

        initialQuestIndex(dateKey) {
            const seed = Array.from(dateKey).reduce((total, character) => total + character.charCodeAt(0), 0);
            return seed % DAILY_QUESTS.length;
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
                answersCompleted: this.boundedCount(stored.answersCompleted),
                streakDays: this.boundedCount(stored.streakDays, 3650),
                lastAnswerDate: this.normalizeDate(stored.lastAnswerDate),
                dailyQuestDate: this.normalizeDate(stored.dailyQuestDate),
                dailyQuestIndex: this.boundedCount(stored.dailyQuestIndex, DAILY_QUESTS.length - 1),
                dailyQuestCompleted: stored.dailyQuestCompleted === true,
                interactionCount: this.boundedCount(stored.interactionCount, 999999),
            };
        }

        ensureDailyQuest() {
            const today = this.todayKey();
            if (this.profile.dailyQuestDate === today) {
                return false;
            }
            this.profile.dailyQuestDate = today;
            this.profile.dailyQuestIndex = this.initialQuestIndex(today);
            this.profile.dailyQuestCompleted = false;
            return true;
        }

        load() {
            try {
                const current = localStorage.getItem(STORAGE_KEY);
                const legacy = LEGACY_STORAGE_KEYS
                    .map((key) => localStorage.getItem(key))
                    .find((value) => Boolean(value));
                this.profile = this.normalizeProfile(JSON.parse(current || legacy || "{}"));
                const questChanged = this.ensureDailyQuest();
                if (!current || questChanged) {
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
            if (this.root) {
                this.root.dataset.stage = stage.key;
            }
            if (this.stageHint) {
                this.stageHint.textContent = nextStage
                    ? `再积累 ${nextStage.minimum - this.profile.bond}% 默契，就会成为${nextStage.name}。`
                    : "默契已经满格，继续一起探索吧。";
            }
        }

        renderFootprint() {
            if (this.answersValue) {
                this.answersValue.textContent = String(this.profile.answersCompleted);
            }
            if (this.streakValue) {
                this.streakValue.textContent = `${this.profile.streakDays} 天`;
            }
        }

        renderQuest() {
            if (this.ensureDailyQuest()) {
                this.persist();
            }
            if (this.questText) {
                this.questText.textContent = DAILY_QUESTS[this.profile.dailyQuestIndex];
            }
            if (this.questStatus) {
                this.questStatus.textContent = this.profile.dailyQuestCompleted ? "今日完成" : "待完成";
            }
            if (this.questCompleteButton) {
                this.questCompleteButton.disabled = this.profile.dailyQuestCompleted;
                this.questCompleteButton.textContent = this.profile.dailyQuestCompleted ? "已完成" : "完成啦";
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
            this.renderFootprint();
            this.renderQuest();
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

        celebrate() {
            if (!this.root) {
                return;
            }
            if (this.celebrationTimer) {
                globalThis.clearTimeout(this.celebrationTimer);
            }
            this.root.classList.remove("is-celebrating");
            void this.root.offsetWidth;
            this.root.classList.add("is-celebrating");
            this.celebrationTimer = globalThis.setTimeout(() => {
                this.root?.classList.remove("is-celebrating");
                this.celebrationTimer = null;
            }, 900);
        }

        nextReaction(action) {
            const temperament = REACTIONS[this.profile.temperament] || REACTIONS.calm;
            const choices = temperament[action] || temperament.pet;
            const message = choices[this.profile.interactionCount % choices.length];
            this.profile.interactionCount = this.boundedCount(this.profile.interactionCount + 1, 999999);
            return message;
        }

        updateLearningStreak(today) {
            if (this.profile.lastAnswerDate === today) {
                return;
            }
            const previous = this.profile.lastAnswerDate
                ? Date.parse(`${this.profile.lastAnswerDate}T00:00:00Z`)
                : Number.NaN;
            const current = Date.parse(`${today}T00:00:00Z`);
            this.profile.streakDays = Number.isFinite(previous) && current - previous === 86400000
                ? this.boundedCount(this.profile.streakDays + 1, 3650)
                : 1;
            this.profile.lastAnswerDate = today;
        }

        recordAnswerCompleted() {
            if (this.completionRecordedForActiveRequest) {
                this.requestActive = false;
                return false;
            }
            this.completionRecordedForActiveRequest = true;
            this.requestActive = false;
            this.ensureDailyQuest();
            this.updateLearningStreak(this.todayKey());
            this.profile.answersCompleted = this.boundedCount(this.profile.answersCompleted + 1);
            const milestone = this.adjustBond(3);
            this.renderFootprint();
            this.renderQuest();
            this.persist();
            this.celebrate();
            const message = milestone
                ? `解锁${milestone.name}装扮！我们已经一起完成 ${this.profile.answersCompleted} 个问题。`
                : `一起完成第 ${this.profile.answersCompleted} 个问题，连续探索 ${this.profile.streakDays} 天！`;
            this.setState("happy", message, { resetAfterMs: 4200 });
            return true;
        }

        setRequestActive(active) {
            const nextActive = Boolean(active);
            if (nextActive && !this.requestActive) {
                this.completionRecordedForActiveRequest = false;
            }
            this.requestActive = nextActive;
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
                this.panel.scrollTop = 0;
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
                const reaction = this.nextReaction("pet");
                milestone = this.adjustBond(4);
                this.setState("happy", reaction, { resetAfterMs: 2400 });
                this.celebrate();
            } else if (normalizedAction === "inspire") {
                const reaction = this.nextReaction("inspire");
                milestone = this.adjustBond(7);
                this.setState("happy", reaction, { resetAfterMs: 2800 });
                this.celebrate();
                this.chatQuestion?.focus();
            } else if (normalizedAction === "reroll-quest") {
                if (this.profile.dailyQuestCompleted) {
                    this.setState("idle", "今天的灵感签已经完成啦，明天再来抽一张。", { resetAfterMs: 2200 });
                    return;
                }
                this.profile.dailyQuestIndex = (this.profile.dailyQuestIndex + 1) % DAILY_QUESTS.length;
                this.persist();
                this.renderQuest();
                this.setState("idle", "换好啦，挑一个让你有点好奇的小挑战吧。", { resetAfterMs: 2200 });
            } else if (normalizedAction === "complete-quest") {
                if (this.profile.dailyQuestCompleted) {
                    return;
                }
                this.profile.dailyQuestCompleted = true;
                milestone = this.adjustBond(5);
                this.renderQuest();
                this.persist();
                this.celebrate();
                this.setState("happy", "今日灵感签完成，奖励 5% 默契！", { resetAfterMs: 3200 });
            } else if (normalizedAction === "save-name") {
                this.saveName();
            } else if (normalizedAction === "hide") {
                this.hide();
            } else {
                return;
            }
            if (milestone) {
                this.celebrate();
                this.setState(
                    "happy",
                    `新的成长阶段：${milestone.name}！解锁了一件新装扮。`,
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
