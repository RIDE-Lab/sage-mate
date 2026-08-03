(() => {
    "use strict";

    const STORAGE_KEY = "sageMateCompanion:v1";
    const DEFAULT_BOND = 20;
    const IDLE_MESSAGE = "我在这里陪你，随时可以开始一个新问题。";
    const VALID_STATES = new Set(["idle", "thinking", "happy", "worried"]);

    class SageCompanionController {
        constructor({ chatForm, chatShell, chatQuestion }) {
            this.root = document.getElementById("sage-companion");
            this.toggle = document.getElementById("sage-companion-toggle");
            this.panel = document.getElementById("sage-companion-panel");
            this.closeButton = document.getElementById("sage-companion-close");
            this.message = document.getElementById("sage-companion-message");
            this.bondValue = document.getElementById("sage-companion-bond-value");
            this.bondBar = document.getElementById("sage-companion-bond-bar");
            this.chatForm = chatForm;
            this.chatShell = chatShell;
            this.chatQuestion = chatQuestion;
            this.bond = DEFAULT_BOND;
            this.requestActive = false;
            this.resetTimer = null;
            this.resizeObserver = null;
            this.classObserver = null;
            this.initialized = false;
        }

        clampBond(value) {
            const parsed = Number(value);
            if (!Number.isFinite(parsed)) {
                return DEFAULT_BOND;
            }
            return Math.min(100, Math.max(0, Math.round(parsed)));
        }

        load() {
            try {
                const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
                this.bond = this.clampBond(stored.bond);
            } catch {
                this.bond = DEFAULT_BOND;
            }
        }

        persist() {
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify({ bond: this.bond }));
            } catch {
                // Keep the companion usable when browser storage is unavailable.
            }
        }

        renderBond() {
            if (this.bondValue) {
                this.bondValue.textContent = `${this.bond}%`;
            }
            if (this.bondBar) {
                this.bondBar.style.width = `${this.bond}%`;
            }
        }

        syncToggleLabel() {
            if (!this.toggle) {
                return;
            }
            const message = this.message?.textContent?.trim() || IDLE_MESSAGE;
            const action = this.toggle.getAttribute("aria-expanded") === "true" ? "关闭" : "打开";
            this.toggle.setAttribute("aria-label", `小 Sage，当前状态：${message} 点击${action}伙伴面板`);
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
            const normalizedMessage = String(message || IDLE_MESSAGE).trim();
            this.root.dataset.state = normalizedState;
            if (this.message) {
                this.message.textContent = normalizedMessage;
            }
            this.syncToggleLabel();
            if (resetAfterMs > 0) {
                this.resetTimer = globalThis.setTimeout(() => {
                    this.resetTimer = null;
                    if (!this.requestActive) {
                        this.setState("idle", IDLE_MESSAGE);
                    }
                }, resetAfterMs);
            }
        }

        setPanelOpen(open) {
            if (!this.panel || !this.toggle) {
                return;
            }
            const nextOpen = Boolean(open);
            this.panel.hidden = !nextOpen;
            this.toggle.setAttribute("aria-expanded", String(nextOpen));
            this.syncToggleLabel();
            if (nextOpen) {
                this.closeButton?.focus();
            } else {
                this.toggle.focus();
            }
        }

        handleAction(action) {
            const normalizedAction = String(action || "");
            if (normalizedAction === "pet") {
                this.bond = this.clampBond(this.bond + 4);
                this.setState("happy", "呼噜呼噜，谢谢你！我会继续认真陪你。", { resetAfterMs: 2200 });
            } else if (normalizedAction === "inspire") {
                this.bond = this.clampBond(this.bond + 7);
                this.setState("happy", "灵感充满啦！试着把现在最想解决的问题告诉我吧。", { resetAfterMs: 2600 });
                this.chatQuestion?.focus();
            } else {
                return;
            }
            this.renderBond();
            this.persist();
        }

        syncPlacement() {
            if (!this.root || !this.chatForm || !this.chatShell) {
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

        init() {
            if (this.initialized || !this.root || !this.toggle || !this.panel) {
                return;
            }
            this.initialized = true;
            this.load();
            this.renderBond();
            this.setState("idle", IDLE_MESSAGE);
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
            this.panel.addEventListener("click", (event) => {
                const actionButton = event.target.closest("[data-companion-action]");
                if (actionButton) {
                    this.handleAction(actionButton.dataset.companionAction);
                }
            });
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
