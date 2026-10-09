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
        positionX: null,
        positionY: null,
    });
    const DRAG_THRESHOLD_PX = 6;
    const MANUAL_WANDER_PAUSE_MS = 12000;
    const WANDER_DELAY_MIN_MS = 4200;
    const WANDER_DELAY_MAX_MS = 8200;
    const WANDER_DURATION_MIN_MS = 2600;
    const WANDER_DURATION_MAX_MS = 4600;
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
            this.stageChip = document.getElementById("sage-companion-stage-chip");
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
            this.tabs = Array.from(this.panel?.querySelectorAll("[data-companion-tab]") || []);
            this.tabPanels = Array.from(this.panel?.querySelectorAll("[data-companion-panel]") || []);
            this.scrollArea = document.getElementById("sage-companion-scroll-area");
            this.scrollCue = document.getElementById("sage-companion-scroll-cue");
            this.chatForm = chatForm;
            this.chatShell = chatShell;
            this.chatQuestion = chatQuestion;
            this.profile = { ...DEFAULT_PROFILE };
            this.requestActive = false;
            this.completionRecordedForActiveRequest = false;
            this.resetTimer = null;
            this.celebrationTimer = null;
            this.panelAnimationTimer = null;
            this.tabAnimationTimer = null;
            this.bondAnimationTimer = null;
            this.lastRenderedBond = null;
            this.activeTab = "companion";
            this.resizeObserver = null;
            this.classObserver = null;
            this.motionQuery = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)") || null;
            this.wanderTimer = null;
            this.walkFinishTimer = null;
            this.manualWanderPauseUntil = 0;
            this.dragPointerId = null;
            this.dragStart = null;
            this.isDragging = false;
            this.suppressNextClick = false;
            this.hasFreePosition = false;
            this.lastChatWasEmpty = null;
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

        normalizeUnitPosition(value) {
            if (value === null || value === undefined || value === "") {
                return null;
            }
            const parsed = Number(value);
            return Number.isFinite(parsed) ? Math.min(1, Math.max(0, parsed)) : null;
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
                positionX: this.normalizeUnitPosition(stored.positionX),
                positionY: this.normalizeUnitPosition(stored.positionY),
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
            if (this.stageChip) {
                this.stageChip.textContent = stage.name;
            }
            if (this.root) {
                this.root.dataset.stage = stage.key;
            }
            if (this.stageHint) {
                this.stageHint.textContent = nextStage
                    ? `再积累 ${nextStage.minimum - this.profile.bond}% 默契，就会成为${nextStage.name}。`
                    : "默契已经满格，继续一起探索吧。";
            }
            if (this.lastRenderedBond !== null && this.lastRenderedBond !== this.profile.bond) {
                if (this.bondAnimationTimer) {
                    globalThis.clearTimeout(this.bondAnimationTimer);
                }
                this.panel?.classList.remove("is-bond-updating");
                void this.panel?.offsetWidth;
                this.panel?.classList.add("is-bond-updating");
                this.bondAnimationTimer = globalThis.setTimeout(() => {
                    this.panel?.classList.remove("is-bond-updating");
                    this.bondAnimationTimer = null;
                }, 620);
            }
            this.lastRenderedBond = this.profile.bond;
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
                `${this.profile.name}，当前状态：${message} 点击${action}伙伴面板，也可以拖动位置`
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
            if (nextActive) {
                this.pauseWandering(1200);
            } else {
                this.scheduleWander(2400);
            }
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

        selectTab(tabName, { focus = false, animate = true } = {}) {
            const requestedTab = String(tabName || "companion");
            const selectedTab = this.tabs.find((tab) => tab.dataset.companionTab === requestedTab)
                ? requestedTab
                : "companion";
            this.activeTab = selectedTab;
            this.root.dataset.activeTab = selectedTab;
            this.tabs.forEach((tab) => {
                const selected = tab.dataset.companionTab === selectedTab;
                tab.setAttribute("aria-selected", String(selected));
                tab.tabIndex = selected ? 0 : -1;
                if (selected && focus) {
                    tab.focus();
                }
            });
            this.tabPanels.forEach((tabPanel) => {
                const selected = tabPanel.dataset.companionPanel === selectedTab;
                tabPanel.hidden = !selected;
                tabPanel.classList.remove("is-entering");
                if (selected && animate) {
                    void tabPanel.offsetWidth;
                    tabPanel.classList.add("is-entering");
                }
            });
            if (this.tabAnimationTimer) {
                globalThis.clearTimeout(this.tabAnimationTimer);
            }
            this.tabAnimationTimer = globalThis.setTimeout(() => {
                this.tabPanels.forEach((tabPanel) => tabPanel.classList.remove("is-entering"));
                this.tabAnimationTimer = null;
            }, 260);
            if (this.scrollArea) {
                this.scrollArea.scrollTop = 0;
            }
            // The customize panel can be taller than the companion panel,
            // especially with fallback fonts. Recompute the available outer
            // height synchronously so a fast test/user click cannot observe a
            // stale geometry before the next animation frame.
            this.syncPanelPlacement();
            globalThis.requestAnimationFrame(() => {
                this.syncPlacement();
                this.syncPanelPlacement();
                this.syncScrollCue();
            });
        }

        handleTabKeydown(event) {
            const currentIndex = this.tabs.indexOf(event.currentTarget);
            if (currentIndex < 0) {
                return;
            }
            let nextIndex = currentIndex;
            if (event.key === "ArrowRight") {
                nextIndex = (currentIndex + 1) % this.tabs.length;
            } else if (event.key === "ArrowLeft") {
                nextIndex = (currentIndex - 1 + this.tabs.length) % this.tabs.length;
            } else if (event.key === "Home") {
                nextIndex = 0;
            } else if (event.key === "End") {
                nextIndex = this.tabs.length - 1;
            } else {
                return;
            }
            event.preventDefault();
            this.selectTab(this.tabs[nextIndex].dataset.companionTab, { focus: true });
        }

        syncScrollCue() {
            if (!this.scrollArea || !this.scrollCue || this.panel.hidden) {
                return;
            }
            const remaining = this.scrollArea.scrollHeight - this.scrollArea.clientHeight - this.scrollArea.scrollTop;
            this.scrollCue.hidden = !(this.scrollArea.scrollHeight > this.scrollArea.clientHeight + 8 && remaining > 10);
        }

        movementBounds() {
            const shellRect = this.chatShell?.getBoundingClientRect();
            const toggleRect = this.toggle?.getBoundingClientRect();
            const formRect = this.chatForm?.getBoundingClientRect();
            const contentRect = this.chatShell?.querySelector(".composer-inner")?.getBoundingClientRect();
            const margin = 12;
            const usesViewportPosition = globalThis.matchMedia?.("(max-width: 720px)").matches;
            const chatIsEmpty = this.chatShell?.classList.contains("chat-empty");
            let left = Math.max(margin, shellRect?.left || margin);
            let right = Math.min(globalThis.innerWidth - margin, shellRect?.right || globalThis.innerWidth - margin);
            const top = Math.max(margin, shellRect?.top || margin);
            let bottom = Math.min(
                globalThis.innerHeight - margin,
                (formRect?.top || globalThis.innerHeight) - margin
            );
            // On the narrow empty landing screen, keep the wandering companion
            // above the recommendation card so it never hides a seed question.
            if (usesViewportPosition && this.chatShell?.classList.contains("chat-empty")) {
                const seedRect = document.getElementById("seed-chips")?.getBoundingClientRect();
                if (seedRect && seedRect.height > 0) {
                    bottom = Math.min(bottom, seedRect.top - margin);
                }
            }
            const width = toggleRect?.width || 64;
            const height = toggleRect?.height || 64;
            // Once a conversation starts, reserve the transcript column for
            // reading.  On desktop the companion lives in the right gutter;
            // on narrow screens it docks at the right edge above the composer.
            // The empty landing page keeps the wider roaming area users expect.
            if (!chatIsEmpty) {
                const gutterLeft = (contentRect?.right || right) + 16;
                if (!usesViewportPosition && gutterLeft + width <= right) {
                    left = gutterLeft;
                } else {
                    left = Math.max(left, right - width);
                }
            }
            const maxX = Math.max(left, right - width);
            const maxY = Math.max(top, bottom - height);
            return { minX: left, maxX, minY: top, maxY, width, height };
        }

        clampPosition(left, top) {
            const bounds = this.movementBounds();
            return {
                left: Math.min(bounds.maxX, Math.max(bounds.minX, left)),
                top: Math.min(bounds.maxY, Math.max(bounds.minY, top)),
                bounds,
            };
        }

        setViewportPosition(left, top, { duration = 0 } = {}) {
            if (!this.root) {
                return;
            }
            const clamped = this.clampPosition(left, top);
            const shellRect = this.chatShell?.getBoundingClientRect();
            // The mobile media rule uses `position: fixed`, but the chat shell
            // establishes a containing block; use shell-local coordinates in
            // both layouts so a transformed/positioned shell cannot double
            // offset the companion after a resize or reflow.
            const localLeft = clamped.left - (shellRect?.left || 0);
            const localTop = clamped.top - (shellRect?.top || 0);
            this.root.style.setProperty("--sage-companion-travel-ms", `${Math.max(0, Math.round(duration))}ms`);
            this.root.style.left = `${Math.round(localLeft)}px`;
            this.root.style.top = `${Math.round(localTop)}px`;
            this.root.style.right = "auto";
            this.root.style.bottom = "auto";
            this.hasFreePosition = true;
            return clamped;
        }

        freezeCurrentPosition() {
            if (!this.root || !this.root.getClientRects().length) {
                return null;
            }
            const rect = this.root.getBoundingClientRect();
            this.root.classList.add("is-positioning");
            const position = this.setViewportPosition(rect.left, rect.top);
            void this.root.offsetWidth;
            this.root.classList.remove("is-positioning");
            return position;
        }

        saveManualPosition() {
            if (!this.root) {
                return;
            }
            const rect = this.root.getBoundingClientRect();
            const bounds = this.movementBounds();
            const widthRange = Math.max(1, bounds.maxX - bounds.minX);
            const heightRange = Math.max(1, bounds.maxY - bounds.minY);
            this.profile.positionX = this.normalizeUnitPosition((rect.left - bounds.minX) / widthRange);
            this.profile.positionY = this.normalizeUnitPosition((rect.top - bounds.minY) / heightRange);
            this.persist();
        }

        restoreManualPosition() {
            if (this.profile.positionX === null || this.profile.positionY === null) {
                return false;
            }
            const bounds = this.movementBounds();
            this.setViewportPosition(
                bounds.minX + (bounds.maxX - bounds.minX) * this.profile.positionX,
                bounds.minY + (bounds.maxY - bounds.minY) * this.profile.positionY
            );
            return true;
        }

        randomBetween(minimum, maximum) {
            return minimum + Math.random() * Math.max(0, maximum - minimum);
        }

        canWander() {
            return Boolean(
                this.root
                && this.chatShell?.classList.contains("chat-empty")
                && this.panel?.hidden
                && !this.profile.hidden
                && !this.requestActive
                && !this.isDragging
                && !this.motionQuery?.matches
                && document.visibilityState === "visible"
                && Date.now() >= this.manualWanderPauseUntil
                && this.root.getClientRects().length
                && !this.root.matches(":hover")
                && !this.root.contains(document.activeElement)
            );
        }

        clearWanderTimers() {
            if (this.wanderTimer) {
                globalThis.clearTimeout(this.wanderTimer);
                this.wanderTimer = null;
            }
            if (this.walkFinishTimer) {
                globalThis.clearTimeout(this.walkFinishTimer);
                this.walkFinishTimer = null;
            }
        }

        pauseWandering(duration = 0) {
            this.clearWanderTimers();
            if (this.root?.classList.contains("is-walking")) {
                this.freezeCurrentPosition();
                this.root.classList.remove("is-walking");
            }
            this.manualWanderPauseUntil = Math.max(this.manualWanderPauseUntil, Date.now() + duration);
        }

        scheduleWander(delay = null) {
            if (this.wanderTimer || this.walkFinishTimer || this.motionQuery?.matches) {
                return;
            }
            const wait = delay ?? this.randomBetween(WANDER_DELAY_MIN_MS, WANDER_DELAY_MAX_MS);
            this.wanderTimer = globalThis.setTimeout(() => {
                this.wanderTimer = null;
                if (this.canWander()) {
                    this.beginWander();
                } else {
                    this.scheduleWander(1800);
                }
            }, Math.max(250, wait));
        }

        beginWander() {
            if (!this.canWander()) {
                this.scheduleWander();
                return;
            }
            const current = this.freezeCurrentPosition();
            if (!current) {
                this.scheduleWander();
                return;
            }
            const bounds = current.bounds;
            const walkingBand = Math.min(110, Math.max(24, (bounds.maxY - bounds.minY) * 0.3));
            let targetLeft = this.randomBetween(bounds.minX, bounds.maxX);
            if (Math.abs(targetLeft - current.left) < 48) {
                targetLeft = current.left < (bounds.minX + bounds.maxX) / 2 ? bounds.maxX : bounds.minX;
            }
            const targetTop = this.randomBetween(Math.max(bounds.minY, bounds.maxY - walkingBand), bounds.maxY);
            const duration = this.randomBetween(WANDER_DURATION_MIN_MS, WANDER_DURATION_MAX_MS);
            this.root.dataset.facing = targetLeft < current.left ? "left" : "right";
            this.root.classList.add("is-walking");
            globalThis.requestAnimationFrame(() => {
                this.setViewportPosition(targetLeft, targetTop, { duration });
            });
            this.walkFinishTimer = globalThis.setTimeout(() => {
                this.walkFinishTimer = null;
                this.root?.classList.remove("is-walking");
                this.syncPanelPlacement();
                this.scheduleWander();
            }, duration + 80);
        }

        handlePointerDown(event) {
            if (!event.isPrimary || event.button !== 0 || !this.panel?.hidden) {
                return;
            }
            this.pauseWandering();
            const rect = this.root.getBoundingClientRect();
            this.dragPointerId = event.pointerId;
            this.dragStart = {
                clientX: event.clientX,
                clientY: event.clientY,
                left: rect.left,
                top: rect.top,
            };
            this.toggle.setPointerCapture?.(event.pointerId);
        }

        handlePointerMove(event) {
            if (event.pointerId !== this.dragPointerId || !this.dragStart) {
                return;
            }
            const deltaX = event.clientX - this.dragStart.clientX;
            const deltaY = event.clientY - this.dragStart.clientY;
            if (!this.isDragging && Math.hypot(deltaX, deltaY) < DRAG_THRESHOLD_PX) {
                return;
            }
            if (!this.isDragging) {
                this.isDragging = true;
                this.root.classList.add("is-dragging");
                this.toggle.setAttribute("aria-grabbed", "true");
            }
            event.preventDefault();
            this.root.dataset.facing = deltaX < 0 ? "left" : "right";
            this.setViewportPosition(this.dragStart.left + deltaX, this.dragStart.top + deltaY);
            this.syncPanelPlacement();
        }

        handlePointerEnd(event) {
            if (event.pointerId !== this.dragPointerId) {
                return;
            }
            this.toggle.releasePointerCapture?.(event.pointerId);
            const dragged = this.isDragging;
            this.dragPointerId = null;
            this.dragStart = null;
            this.isDragging = false;
            this.root.classList.remove("is-dragging");
            this.toggle.setAttribute("aria-grabbed", "false");
            if (dragged) {
                this.suppressNextClick = true;
                this.saveManualPosition();
                this.setState("happy", "这里视野不错，我先待在这儿。", { resetAfterMs: 2200 });
                this.manualWanderPauseUntil = Date.now() + MANUAL_WANDER_PAUSE_MS;
            }
            this.scheduleWander(dragged ? MANUAL_WANDER_PAUSE_MS : 1800);
        }

        syncPanelPlacement() {
            if (!this.root || !this.toggle || !this.panel || this.panel.hidden) {
                return;
            }
            const toggleRect = this.toggle.getBoundingClientRect();
            const rootRect = this.root.getBoundingClientRect();
            const shellRect = this.chatShell?.getBoundingClientRect();
            const formRect = this.chatForm?.getBoundingClientRect();
            const margin = 12;
            const panelWidth = Math.min(346, globalThis.innerWidth - margin * 2);
            const panelLeft = Math.min(
                globalThis.innerWidth - margin - panelWidth,
                Math.max(margin, toggleRect.right - panelWidth)
            );
            this.panel.style.left = `${Math.round(panelLeft - rootRect.left)}px`;
            this.panel.style.right = "auto";
            const layoutTop = Math.max(margin, shellRect?.top || margin);
            const layoutBottom = Math.min(globalThis.innerHeight - margin, formRect?.top || globalThis.innerHeight - margin);
            const spaceAbove = Math.max(0, toggleRect.top - layoutTop - margin);
            const spaceBelow = Math.max(0, layoutBottom - toggleRect.bottom - margin);
            const openBelow = spaceAbove < 220 && spaceBelow > spaceAbove;
            this.root.dataset.panelSide = openBelow ? "below" : "above";
            this.root.style.setProperty(
                "--sage-companion-panel-max-height",
                `${Math.max(160, Math.floor(openBelow ? spaceBelow : spaceAbove))}px`
            );
        }

        setPanelOpen(open, { returnFocus = true, tab = null } = {}) {
            if (!this.panel || !this.toggle || this.profile.hidden) {
                return;
            }
            const nextOpen = Boolean(open);
            if (nextOpen) {
                this.pauseWandering(1600);
            }
            this.panel.hidden = !nextOpen;
            this.toggle.setAttribute("aria-expanded", String(nextOpen));
            this.syncToggleLabel();
            if (nextOpen) {
                this.panel.classList.remove("is-opening");
                void this.panel.offsetWidth;
                this.panel.classList.add("is-opening");
                if (this.panelAnimationTimer) {
                    globalThis.clearTimeout(this.panelAnimationTimer);
                }
                this.panelAnimationTimer = globalThis.setTimeout(() => {
                    this.panel?.classList.remove("is-opening");
                    this.panelAnimationTimer = null;
                }, 340);
                this.selectTab(tab || this.activeTab, { focus: true, animate: false });
                globalThis.requestAnimationFrame(() => {
                    this.syncPlacement();
                    this.syncPanelPlacement();
                    this.syncScrollCue();
                });
            } else if (returnFocus) {
                this.scrollCue.hidden = true;
                this.toggle.focus();
            }
            if (!nextOpen) {
                this.scheduleWander(2200);
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
            this.pauseWandering();
            this.setPanelOpen(false, { returnFocus: false });
            this.profile.hidden = true;
            this.persist();
            this.renderProfile();
            this.settingsTrigger?.focus();
        }

        restore(tab = "customize") {
            this.profile.hidden = false;
            this.persist();
            this.renderProfile();
            this.setState("happy", `${this.profile.name}回来啦！我们继续一起探索吧。`, { resetAfterMs: 2200 });
            this.setPanelOpen(true, { tab });
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
            const usesViewportPosition = globalThis.matchMedia?.("(max-width: 720px)").matches;
            const chatIsEmpty = this.chatShell.classList.contains("chat-empty");
            this.root.classList.toggle("is-conversation-docked", !chatIsEmpty);
            const toggleRect = this.toggle?.getBoundingClientRect();
            const layoutBottom = usesViewportPosition ? globalThis.innerHeight : shellRect.bottom;
            const layoutTop = usesViewportPosition ? 0 : shellRect.top;
            const bottomOffset = Math.max(12, Math.ceil(layoutBottom - formRect.top + 12));
            const panelMaxHeight = Math.max(
                160,
                Math.floor(formRect.top - layoutTop - (toggleRect?.height || 64) - 24)
            );
            this.root.style.setProperty("--sage-companion-bottom-offset", `${bottomOffset}px`);
            this.root.style.setProperty("--sage-companion-panel-max-height", `${panelMaxHeight}px`);
            if (this.lastChatWasEmpty !== chatIsEmpty) {
                this.lastChatWasEmpty = chatIsEmpty;
                if (chatIsEmpty) {
                    this.restoreManualPosition();
                    this.scheduleWander(1800);
                } else {
                    this.pauseWandering();
                    const bounds = this.movementBounds();
                    this.root.classList.add("is-positioning");
                    this.setViewportPosition(bounds.maxX, bounds.maxY);
                    void this.root.offsetWidth;
                    this.root.classList.remove("is-positioning");
                }
            }
            if (this.hasFreePosition) {
                const rect = this.root.getBoundingClientRect();
                this.root.classList.add("is-positioning");
                this.setViewportPosition(rect.left, rect.top);
                void this.root.offsetWidth;
                this.root.classList.remove("is-positioning");
            }
            // A restored/wandering viewport position can be stale for one
            // frame while the mobile composer reflows. Clamp it again after
            // measuring the live form so the companion never covers input or
            // recommendation chips.
            if (usesViewportPosition && chatIsEmpty && !this.root.classList.contains("is-dragging")) {
                const rect = this.root.getBoundingClientRect();
                const bounds = this.movementBounds();
                if (rect.bottom > formRect.top - 12 || rect.top > bounds.maxY) {
                    this.root.classList.add("is-positioning");
                    this.setViewportPosition(rect.left, bounds.maxY);
                    void this.root.offsetWidth;
                    this.root.classList.remove("is-positioning");
                }
            }
            globalThis.requestAnimationFrame(() => {
                this.syncPanelPlacement();
                this.syncScrollCue();
            });
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
            globalThis.requestAnimationFrame(() => {
                this.restoreManualPosition();
                this.syncPlacement();
                this.scheduleWander(2600);
            });
            globalThis.addEventListener("resize", () => {
                this.pauseWandering(900);
                this.syncPlacement();
                this.scheduleWander(1200);
            });
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
            this.toggle.addEventListener("pointerdown", (event) => this.handlePointerDown(event));
            this.toggle.addEventListener("pointermove", (event) => this.handlePointerMove(event));
            this.toggle.addEventListener("pointerup", (event) => this.handlePointerEnd(event));
            this.toggle.addEventListener("pointercancel", (event) => this.handlePointerEnd(event));
            this.toggle.addEventListener("dragstart", (event) => event.preventDefault());
            this.toggle.addEventListener("click", () => {
                if (this.suppressNextClick) {
                    this.suppressNextClick = false;
                    return;
                }
                this.setPanelOpen(this.panel.hidden, { tab: "companion" });
            });
            this.root.addEventListener("pointerenter", () => this.pauseWandering(1400));
            this.root.addEventListener("pointerleave", () => this.scheduleWander(1400));
            this.root.addEventListener("focusin", () => this.pauseWandering(1600));
            this.root.addEventListener("focusout", () => this.scheduleWander(1800));
            this.closeButton?.addEventListener("click", () => this.setPanelOpen(false));
            this.settingsTrigger?.addEventListener("click", () => {
                if (this.profile.hidden) {
                    this.restore("customize");
                } else {
                    this.setPanelOpen(true, { tab: "customize" });
                }
            });
            this.tabs.forEach((tab) => {
                tab.addEventListener("click", () => this.selectTab(tab.dataset.companionTab));
                tab.addEventListener("keydown", (event) => this.handleTabKeydown(event));
            });
            this.scrollArea?.addEventListener("scroll", () => this.syncScrollCue(), { passive: true });
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
            document.addEventListener("visibilitychange", () => {
                if (document.visibilityState === "visible") {
                    this.scheduleWander(1800);
                } else {
                    this.pauseWandering();
                }
            });
            this.motionQuery?.addEventListener?.("change", () => {
                if (this.motionQuery.matches) {
                    this.pauseWandering();
                } else {
                    this.scheduleWander(1800);
                }
            });
        }
    }

    globalThis.SageCompanion = Object.freeze({
        create(options) {
            return new SageCompanionController(options);
        },
    });
})();
