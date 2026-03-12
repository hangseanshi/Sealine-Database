/* ═══════════════════════════════════════════════════════════════════════════
   Sealine Agent — Chat Interface
   ═══════════════════════════════════════════════════════════════════════════ */

(function () {
    "use strict";

    // ── State ────────────────────────────────────────────────────────────────
    const state = {
        activeSessionId: null,
        conversations: [],       // [{id, title, createdAt, lastMessageAt}]
        isLoading: false,
    };

    // ── DOM Refs ─────────────────────────────────────────────────────────────
    const $ = (sel) => document.querySelector(sel);
    const dom = {
        sidebar:          $("#sidebar"),
        sidebarOverlay:   $("#sidebar-overlay"),
        menuBtn:          $("#menu-btn"),
        newChatBtn:       $("#new-chat-btn"),
        convList:         $("#conversation-list"),
        welcomeScreen:    $("#welcome-screen"),
        messages:         $("#messages"),
        input:            $("#message-input"),
        sendBtn:          $("#send-btn"),
        inputArea:        $("#input-area"),
    };

    // ── API Layer ────────────────────────────────────────────────────────────
    const api = {
        async createSession() {
            const res = await fetch("/sessions", { method: "POST" });
            if (!res.ok) throw await _apiError(res);
            return res.json();
        },

        async sendMessage(sessionId, message) {
            const res = await fetch(`/sessions/${sessionId}/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ message }),
                signal: AbortSignal.timeout(120000),
            });
            if (!res.ok) throw await _apiError(res);
            return res.json();
        },

        async listSessions() {
            const res = await fetch("/sessions");
            if (!res.ok) throw await _apiError(res);
            return res.json();
        },

        async deleteSession(sessionId) {
            const res = await fetch(`/sessions/${sessionId}`, { method: "DELETE" });
            if (res.status === 404) return; // already gone
            if (!res.ok) throw await _apiError(res);
        },
    };

    async function _apiError(res) {
        let detail = `HTTP ${res.status}`;
        try {
            const body = await res.json();
            detail = body.detail || detail;
        } catch (_) {}
        const err = new Error(detail);
        err.status = res.status;
        return err;
    }

    // ── Storage Layer (localStorage) ─────────────────────────────────────────
    const STORAGE_KEY = "sealine_conversations";
    const MSG_PREFIX  = "sealine_messages_";

    const storage = {
        getConversations() {
            try {
                return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
            } catch { return []; }
        },

        saveConversations(convs) {
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify(convs));
            } catch {
                // quota exceeded — silently drop oldest message caches
                _evictOldest(convs);
            }
        },

        getMessages(sessionId) {
            try {
                return JSON.parse(localStorage.getItem(MSG_PREFIX + sessionId) || "[]");
            } catch { return []; }
        },

        saveMessages(sessionId, messages) {
            try {
                localStorage.setItem(MSG_PREFIX + sessionId, JSON.stringify(messages));
            } catch {
                // quota exceeded
            }
        },

        removeMessages(sessionId) {
            localStorage.removeItem(MSG_PREFIX + sessionId);
        },

        addConversation(id, title) {
            const now = new Date().toISOString();
            const conv = { id, title, createdAt: now, lastMessageAt: now };
            state.conversations.unshift(conv);
            storage.saveConversations(state.conversations);
            return conv;
        },

        updateConversation(id, updates) {
            const conv = state.conversations.find((c) => c.id === id);
            if (conv) Object.assign(conv, updates);
            storage.saveConversations(state.conversations);
        },

        removeConversation(id) {
            state.conversations = state.conversations.filter((c) => c.id !== id);
            storage.saveConversations(state.conversations);
            storage.removeMessages(id);
        },
    };

    function _evictOldest(convs) {
        if (convs.length > 1) {
            const oldest = convs[convs.length - 1];
            localStorage.removeItem(MSG_PREFIX + oldest.id);
        }
    }

    // ── Markdown Renderer ────────────────────────────────────────────────────
    function renderMarkdown(text) {
        try {
            marked.setOptions({
                breaks: true,
                gfm: true,
                highlight(code, lang) {
                    if (lang && hljs.getLanguage(lang)) {
                        return hljs.highlight(code, { language: lang }).value;
                    }
                    return hljs.highlightAuto(code).value;
                },
            });
            const raw = marked.parse(text);
            return DOMPurify.sanitize(raw);
        } catch {
            return escapeHtml(text);
        }
    }

    function escapeHtml(text) {
        const div = document.createElement("div");
        div.textContent = text;
        return div.innerHTML;
    }

    // ── UI Layer ─────────────────────────────────────────────────────────────
    const ui = {
        renderSidebar() {
            dom.convList.innerHTML = "";
            for (const conv of state.conversations) {
                const item = document.createElement("div");
                item.className = "conversation-item" + (conv.id === state.activeSessionId ? " active" : "");
                item.innerHTML = `
                    <span class="conv-title">${escapeHtml(conv.title || "New conversation")}</span>
                    <button class="conv-delete" title="Delete">&times;</button>
                `;
                item.querySelector(".conv-title").addEventListener("click", () => chat.switchTo(conv.id));
                item.querySelector(".conv-delete").addEventListener("click", (e) => {
                    e.stopPropagation();
                    chat.deleteConversation(conv.id);
                });
                dom.convList.appendChild(item);
            }
        },

        renderMessages(msgs) {
            dom.messages.innerHTML = "";
            for (const msg of msgs) {
                ui.appendMessage(msg.role, msg.content, false);
            }
            // Add loading placeholder at the end
            const loadingEl = document.createElement("div");
            loadingEl.className = "loading-message";
            loadingEl.id = "loading-indicator";
            loadingEl.innerHTML = `
                <div class="message-label">Sealine Agent</div>
                <div class="typing-indicator"><span></span><span></span><span></span></div>
            `;
            dom.messages.appendChild(loadingEl);
            ui.scrollToBottom();
        },

        appendMessage(role, content, scroll = true) {
            const el = document.createElement("div");
            el.className = `message ${role}`;

            const label = role === "user" ? "You" : "Sealine Agent";
            const rendered = role === "assistant" ? renderMarkdown(content) : escapeHtml(content);

            el.innerHTML = `
                <div class="message-label">${label}</div>
                <div class="message-content">${rendered}</div>
            `;

            // Insert before loading indicator if it exists
            const loader = document.getElementById("loading-indicator");
            if (loader) {
                dom.messages.insertBefore(el, loader);
            } else {
                dom.messages.appendChild(el);
            }

            // Add copy buttons to code blocks
            if (role === "assistant") {
                el.querySelectorAll("pre").forEach((pre) => {
                    const wrapper = document.createElement("div");
                    wrapper.className = "code-block-wrapper";
                    pre.parentNode.insertBefore(wrapper, pre);
                    wrapper.appendChild(pre);

                    const btn = document.createElement("button");
                    btn.className = "copy-btn";
                    btn.textContent = "Copy";
                    btn.addEventListener("click", () => {
                        const code = pre.querySelector("code");
                        navigator.clipboard.writeText(code ? code.textContent : pre.textContent);
                        btn.textContent = "Copied!";
                        setTimeout(() => (btn.textContent = "Copy"), 1500);
                    });
                    wrapper.appendChild(btn);
                });
            }

            if (scroll) ui.scrollToBottom();
        },

        showLoading() {
            const el = document.getElementById("loading-indicator");
            if (el) el.classList.add("visible");
            ui.scrollToBottom();
        },

        hideLoading() {
            const el = document.getElementById("loading-indicator");
            if (el) el.classList.remove("visible");
        },

        showWelcome() {
            dom.welcomeScreen.classList.remove("hidden");
            dom.messages.classList.remove("visible");
        },

        hideWelcome() {
            dom.welcomeScreen.classList.add("hidden");
            dom.messages.classList.add("visible");
        },

        showError(message) {
            const banner = document.createElement("div");
            banner.className = "error-banner";
            banner.textContent = message;
            const loader = document.getElementById("loading-indicator");
            if (loader) {
                dom.messages.insertBefore(banner, loader);
            } else {
                dom.messages.appendChild(banner);
            }
            ui.scrollToBottom();
            setTimeout(() => banner.remove(), 6000);
        },

        scrollToBottom() {
            requestAnimationFrame(() => {
                dom.messages.scrollTop = dom.messages.scrollHeight;
            });
        },

        toggleSidebar(open) {
            dom.sidebar.classList.toggle("open", open);
            dom.sidebarOverlay.classList.toggle("visible", open);
        },

        updateSendBtn() {
            dom.sendBtn.disabled = !dom.input.value.trim() || state.isLoading;
        },

        autoGrow() {
            dom.input.style.height = "auto";
            dom.input.style.height = Math.min(dom.input.scrollHeight, 200) + "px";
        },

        clearInput() {
            dom.input.value = "";
            dom.input.style.height = "auto";
            ui.updateSendBtn();
        },
    };

    // ── Chat Controller ──────────────────────────────────────────────────────
    const chat = {
        async newConversation() {
            try {
                const session = await api.createSession();
                const conv = storage.addConversation(session.session_id, "New conversation");
                state.activeSessionId = conv.id;

                // Initialize empty messages and loading indicator
                dom.messages.innerHTML = "";
                const loadingEl = document.createElement("div");
                loadingEl.className = "loading-message";
                loadingEl.id = "loading-indicator";
                loadingEl.innerHTML = `
                    <div class="message-label">Sealine Agent</div>
                    <div class="typing-indicator"><span></span><span></span><span></span></div>
                `;
                dom.messages.appendChild(loadingEl);

                ui.showWelcome();
                ui.renderSidebar();
                dom.input.focus();
                ui.toggleSidebar(false);
            } catch (err) {
                ui.showError("Failed to create session: " + err.message);
            }
        },

        async switchTo(sessionId) {
            state.activeSessionId = sessionId;
            const msgs = storage.getMessages(sessionId);

            if (msgs.length > 0) {
                ui.hideWelcome();
                ui.renderMessages(msgs);
            } else {
                ui.renderMessages([]);
                ui.showWelcome();
            }

            ui.renderSidebar();
            ui.toggleSidebar(false);
            dom.input.focus();
        },

        async sendMessage(text) {
            if (!text.trim() || state.isLoading) return;

            // Create session if needed
            if (!state.activeSessionId) {
                try {
                    const session = await api.createSession();
                    const conv = storage.addConversation(session.session_id, "New conversation");
                    state.activeSessionId = conv.id;

                    // Set up messages container with loading indicator
                    dom.messages.innerHTML = "";
                    const loadingEl = document.createElement("div");
                    loadingEl.className = "loading-message";
                    loadingEl.id = "loading-indicator";
                    loadingEl.innerHTML = `
                        <div class="message-label">Sealine Agent</div>
                        <div class="typing-indicator"><span></span><span></span><span></span></div>
                    `;
                    dom.messages.appendChild(loadingEl);
                    ui.renderSidebar();
                } catch (err) {
                    ui.showError("Failed to create session: " + err.message);
                    return;
                }
            }

            ui.hideWelcome();
            ui.appendMessage("user", text);
            ui.clearInput();
            ui.showLoading();
            state.isLoading = true;
            ui.updateSendBtn();

            // Check if this is the first real message (for title generation)
            const cachedMsgs = storage.getMessages(state.activeSessionId);
            const isFirstMessage = cachedMsgs.length === 0;

            // Save user message immediately
            cachedMsgs.push({ role: "user", content: text });
            storage.saveMessages(state.activeSessionId, cachedMsgs);

            try {
                const data = await api.sendMessage(state.activeSessionId, text);

                // Save assistant response
                cachedMsgs.push({ role: "assistant", content: data.response });
                storage.saveMessages(state.activeSessionId, cachedMsgs);

                ui.hideLoading();
                ui.appendMessage("assistant", data.response);

                // Update title from first message
                if (isFirstMessage) {
                    const title = text.length > 50 ? text.substring(0, 50) + "..." : text;
                    storage.updateConversation(state.activeSessionId, {
                        title,
                        lastMessageAt: new Date().toISOString(),
                    });
                } else {
                    storage.updateConversation(state.activeSessionId, {
                        lastMessageAt: new Date().toISOString(),
                    });
                }

                ui.renderSidebar();
            } catch (err) {
                ui.hideLoading();

                if (err.status === 404) {
                    // Session expired — create new one and retry
                    ui.showError("Session expired. Reconnecting...");
                    try {
                        const newSession = await api.createSession();
                        const oldId = state.activeSessionId;
                        state.activeSessionId = newSession.session_id;

                        // Update the conversation entry
                        const conv = state.conversations.find((c) => c.id === oldId);
                        if (conv) {
                            conv.id = newSession.session_id;
                            storage.saveConversations(state.conversations);
                            // Move messages to new key
                            const oldMsgs = storage.getMessages(oldId);
                            storage.saveMessages(newSession.session_id, oldMsgs);
                            storage.removeMessages(oldId);
                        }

                        // Retry the message
                        const retryData = await api.sendMessage(newSession.session_id, text);
                        const msgs = storage.getMessages(newSession.session_id);
                        msgs.push({ role: "assistant", content: retryData.response });
                        storage.saveMessages(newSession.session_id, msgs);
                        ui.appendMessage("assistant", retryData.response);
                        ui.renderSidebar();
                    } catch (retryErr) {
                        ui.showError("Failed to reconnect: " + retryErr.message);
                    }
                } else {
                    ui.showError("Error: " + err.message);
                }
            } finally {
                state.isLoading = false;
                ui.updateSendBtn();
                dom.input.focus();
            }
        },

        async deleteConversation(sessionId) {
            // Delete from backend (ignore 404)
            api.deleteSession(sessionId).catch(() => {});

            // Remove from storage
            storage.removeConversation(sessionId);

            // If we deleted the active conversation, switch or show welcome
            if (state.activeSessionId === sessionId) {
                state.activeSessionId = null;
                if (state.conversations.length > 0) {
                    chat.switchTo(state.conversations[0].id);
                } else {
                    dom.messages.innerHTML = "";
                    ui.showWelcome();
                }
            }

            ui.renderSidebar();
        },
    };

    // ── Validate sessions on startup ─────────────────────────────────────────
    async function validateSessions() {
        try {
            const activeSessions = await api.listSessions();
            const activeIds = new Set(activeSessions.map((s) => s.session_id));

            // Mark expired sessions — we keep them for history but note they're stale
            for (const conv of state.conversations) {
                conv._expired = !activeIds.has(conv.id);
            }
        } catch {
            // Can't reach server — mark all as potentially expired
        }
    }

    // ── Event Binding ────────────────────────────────────────────────────────
    function bindEvents() {
        // New chat
        dom.newChatBtn.addEventListener("click", () => chat.newConversation());

        // Send message
        dom.sendBtn.addEventListener("click", () => chat.sendMessage(dom.input.value));

        // Keyboard: Enter to send, Shift+Enter for newline
        dom.input.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (dom.input.value.trim() && !state.isLoading) {
                    chat.sendMessage(dom.input.value);
                }
            }
        });

        // Auto-grow textarea
        dom.input.addEventListener("input", () => {
            ui.autoGrow();
            ui.updateSendBtn();
        });

        // Starter prompts
        document.querySelectorAll(".starter").forEach((btn) => {
            btn.addEventListener("click", () => {
                const prompt = btn.getAttribute("data-prompt");
                chat.sendMessage(prompt);
            });
        });

        // Mobile sidebar toggle
        dom.menuBtn.addEventListener("click", () => {
            const isOpen = dom.sidebar.classList.contains("open");
            ui.toggleSidebar(!isOpen);
        });

        dom.sidebarOverlay.addEventListener("click", () => ui.toggleSidebar(false));
    }

    // ── Init ─────────────────────────────────────────────────────────────────
    async function init() {
        // Load conversations from localStorage
        state.conversations = storage.getConversations();

        // Validate which sessions still exist on the backend
        await validateSessions();

        // Render sidebar
        ui.renderSidebar();

        // If we have conversations, load the most recent one
        if (state.conversations.length > 0) {
            await chat.switchTo(state.conversations[0].id);
        } else {
            ui.showWelcome();
        }

        // Bind events
        bindEvents();

        // Focus input
        dom.input.focus();
    }

    // ── Boot ─────────────────────────────────────────────────────────────────
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
