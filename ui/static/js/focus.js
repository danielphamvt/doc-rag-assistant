(function() {
    'use strict';

    if (window.__focus_js_initialized__) return;
    window.__focus_js_initialized__ = true;

    // 1. Force light color scheme (no DOM observers, purely stylistic)
    document.documentElement.style.colorScheme = 'light';

    // 2. Autofocus input textarea after DOM loads
    function focusInput() {
        var el = document.querySelector('#chat-input textarea') || document.querySelector('textarea');
        if (el) el.focus();
    }
    setTimeout(focusInput, 300);
    setTimeout(focusInput, 1000);

    // 3. Track reasoning-box open/closed state
    document.addEventListener('toggle', function(event) {
        if (event.target && (event.target.id === 'reasoning-box' || event.target.classList.contains('reasoning-box'))) {
            window.isReasoningBoxOpen = event.target.open;
        }
    }, true);

    // 4. Custom Sidebar Resizer logic (Zero-lag, No MutationObservers)
    var MIN_SIDEBAR_WIDTH = 180;
    var MAX_SIDEBAR_WIDTH = 650;
    var DEFAULT_SIDEBAR_WIDTH = 280;
    var STORAGE_KEY = 'rag_agent_sidebar_width';

    var isResizing = false;
    var startX = 0;
    var startWidth = DEFAULT_SIDEBAR_WIDTH;
    var currentWidth = DEFAULT_SIDEBAR_WIDTH;

    // Read stored width from localStorage
    try {
        var saved = localStorage.getItem(STORAGE_KEY);
        if (saved) {
            var parsed = parseInt(saved, 10);
            if (!isNaN(parsed) && parsed >= MIN_SIDEBAR_WIDTH && parsed <= MAX_SIDEBAR_WIDTH) {
                currentWidth = parsed;
            }
        }
    } catch (e) {}

    // Below this width the sidebar becomes a top strip; the desktop width lock
    // must never apply there (inline !important would beat the media query)
    var MOBILE_QUERY = window.matchMedia('(max-width: 720px)');

    function clearWidth() {
        var sidebar = document.getElementById('sidebar');
        if (!sidebar) return;
        ['width', 'min-width', 'max-width', 'flex-basis', 'flex-grow', 'flex-shrink']
            .forEach(function(prop) { sidebar.style.removeProperty(prop); });
    }

    function applyWidth(width) {
        var sidebar = document.getElementById('sidebar');
        if (!sidebar) return;
        if (MOBILE_QUERY.matches) { clearWidth(); return; }
        var clamped = Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, width));
        currentWidth = clamped;
        sidebar.style.setProperty('width', clamped + 'px', 'important');
        sidebar.style.setProperty('min-width', clamped + 'px', 'important');
        sidebar.style.setProperty('max-width', clamped + 'px', 'important');
        sidebar.style.setProperty('flex-basis', clamped + 'px', 'important');
        sidebar.style.setProperty('flex-grow', '0', 'important');
        sidebar.style.setProperty('flex-shrink', '0', 'important');
    }

    if (MOBILE_QUERY.addEventListener) {
        MOBILE_QUERY.addEventListener('change', function(e) {
            applyMobilePlaceholder();
            if (e.matches) clearWidth();
            else applyWidth(currentWidth);
        });
    }

    // The full placeholder wraps on a phone-width input while gradio's
    // autosize keeps the box at one line tall — use the short form there
    var DESKTOP_PLACEHOLDER = 'Ask anything about your document corpus...';
    var MOBILE_PLACEHOLDER = 'Ask anything...';
    function applyMobilePlaceholder() {
        var ta = document.querySelector('#chat-input textarea');
        if (ta) ta.placeholder = MOBILE_QUERY.matches ? MOBILE_PLACEHOLDER : DESKTOP_PLACEHOLDER;
    }

    function onPointerMove(clientX) {
        if (!isResizing) return;
        var delta = clientX - startX;
        applyWidth(startWidth + delta);
    }

    function onPointerUp() {
        if (!isResizing) return;
        isResizing = false;
        document.body.classList.remove('is-resizing');
        var resizer = document.getElementById('sidebar-resizer');
        if (resizer) resizer.classList.remove('is-dragging');

        try {
            localStorage.setItem(STORAGE_KEY, currentWidth.toString());
        } catch (e) {}
    }

    window.addEventListener('mousemove', function(e) {
        if (isResizing) onPointerMove(e.clientX);
    }, { passive: true });

    window.addEventListener('mouseup', onPointerUp);
    window.addEventListener('blur', onPointerUp);

    window.addEventListener('touchmove', function(e) {
        if (isResizing && e.touches.length > 0) {
            onPointerMove(e.touches[0].clientX);
        }
    }, { passive: true });

    window.addEventListener('touchend', onPointerUp);
    window.addEventListener('touchcancel', onPointerUp);

    function initResizer() {
        var sidebar = document.getElementById('sidebar');
        var appShell = document.getElementById('app-shell');
        if (!sidebar || !appShell) return false;

        var existing = document.getElementById('sidebar-resizer');
        if (existing) {
            applyWidth(currentWidth);
            return true;
        }

        applyWidth(currentWidth);

        var resizer = document.createElement('div');
        resizer.id = 'sidebar-resizer';
        resizer.className = 'sidebar-resizer';
        resizer.setAttribute('title', 'Kéo để thay đổi kích thước khung bên trái (Nhấp đúp để đặt lại)');

        var handle = document.createElement('div');
        handle.className = 'resizer-handle-pill';
        handle.innerHTML = '<span class="handle-bar"></span>';
        resizer.appendChild(handle);

        var startDrag = function(clientX, e) {
            if (e && e.preventDefault) e.preventDefault();
            var sb = document.getElementById('sidebar');
            if (!sb) return;
            isResizing = true;
            startX = clientX;
            startWidth = sb.getBoundingClientRect().width || currentWidth;
            resizer.classList.add('is-dragging');
            document.body.classList.add('is-resizing');
        };

        resizer.addEventListener('mousedown', function(e) {
            if (e.button !== 0) return;
            startDrag(e.clientX, e);
        });

        resizer.addEventListener('touchstart', function(e) {
            if (e.touches.length > 0) {
                startDrag(e.touches[0].clientX, e);
            }
        }, { passive: false });

        resizer.addEventListener('dblclick', function(e) {
            e.preventDefault();
            applyWidth(DEFAULT_SIDEBAR_WIDTH);
            try {
                localStorage.setItem(STORAGE_KEY, DEFAULT_SIDEBAR_WIDTH.toString());
            } catch (err) {}
        });

        if (sidebar.nextSibling) {
            appShell.insertBefore(resizer, sidebar.nextSibling);
        } else {
            appShell.appendChild(resizer);
        }

        return true;
    }

    // 5. Gemini-style Plus Menu for Source Mode Selection
    function setupPlusMenu() {
        var plusBtn = document.getElementById('plus-btn');
        var dropdown = document.getElementById('plus-dropdown');
        var container = document.getElementById('plus-menu-container');
        if (!plusBtn || !dropdown || !container) return false;

        if (plusBtn.__menu_initialized__) return true;
        plusBtn.__menu_initialized__ = true;

        function setDropdownVisible(open) {
            if (open) {
                dropdown.classList.add('is-open');
                plusBtn.classList.add('is-open');
                plusBtn.setAttribute('aria-expanded', 'true');
            } else {
                dropdown.classList.remove('is-open');
                plusBtn.classList.remove('is-open');
                plusBtn.setAttribute('aria-expanded', 'false');
            }
        }

        plusBtn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            var isOpen = dropdown.classList.contains('is-open');
            setDropdownVisible(!isOpen);
        });

        document.addEventListener('click', function(e) {
            if (!container.contains(e.target)) {
                setDropdownVisible(false);
            }
        });

        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                setDropdownVisible(false);
            }
        });

        function updateChip(mode) {
            var chipContainer = document.getElementById('command-chip-container');
            var chipWrapper = document.getElementById('command-chip-wrapper');
            if (!chipContainer) return;

            if (mode && mode.indexOf('RAG') !== -1) {
                chipContainer.innerHTML = '<div class="cmd-chip"><span class="cmd-chip-text">/search_documents</span><button type="button" class="cmd-chip-remove" title="Bỏ chọn">×</button></div>';
                chipContainer.style.display = 'block';
                if (chipWrapper) chipWrapper.style.setProperty('display', 'flex', 'important');
            } else if (mode && mode.indexOf('Internet') !== -1) {
                chipContainer.innerHTML = '<div class="cmd-chip"><span class="cmd-chip-text">/internet_search</span><button type="button" class="cmd-chip-remove" title="Bỏ chọn">×</button></div>';
                chipContainer.style.display = 'block';
                if (chipWrapper) chipWrapper.style.setProperty('display', 'flex', 'important');
            } else {
                chipContainer.innerHTML = '';
                chipContainer.style.display = 'none';
                if (chipWrapper) chipWrapper.style.setProperty('display', 'none', 'important');
            }

            var removeBtn = chipContainer.querySelector('.cmd-chip-remove');
            if (removeBtn) {
                removeBtn.addEventListener('click', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    selectMode('All sources');
                });
            }
        }

        function setGradioMode(val) {
            var box = document.getElementById('selected-source-mode');
            if (!box) return;
            var el = box.querySelector('textarea') || box.querySelector('input');
            if (el) {
                el.value = val;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }

        function selectMode(targetMode) {
            var items = dropdown.querySelectorAll('.plus-dropdown-item');
            items.forEach(function(item) {
                var m = item.getAttribute('data-mode');
                var isTarget = (m === targetMode);
                if (isTarget) {
                    item.classList.add('active');
                } else {
                    item.classList.remove('active');
                }
            });

            updateChip(targetMode);
            setGradioMode(targetMode);
            setDropdownVisible(false);

            // Refocus text input
            focusInput();
        }

        var items = dropdown.querySelectorAll('.plus-dropdown-item');
        items.forEach(function(item) {
            item.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                var mode = item.getAttribute('data-mode') || 'All sources';
                selectMode(mode);
            });
        });

        // Initialize chip and mode on setup
        updateChip('All sources');

        window.resetSourceMode = function() {
            selectMode('All sources');
        };

        return true;
    }

    // 6. Accessible names for controls gradio renders unnamed: the icon-only
    //    conversation delete button (re-created on every sidebar re-render),
    //    the send/stop circles, and the chat/search textareas ("Textbox")
    function nameControls() {
        document.querySelectorAll('.conv-delete:not([aria-label])').forEach(function(btn) {
            btn.setAttribute('aria-label', 'Delete conversation');
            btn.setAttribute('title', 'Delete conversation');
        });
        var named = [
            ['#chat-input textarea', 'Message'],
            ['#conv-search textarea', 'Search conversations'],
            ['#submit-btn', 'Send message'],
            ['#stop-btn', 'Stop generating']
        ];
        named.forEach(function(pair) {
            var el = document.querySelector(pair[0]);
            if (el && !el.getAttribute('aria-label')) el.setAttribute('aria-label', pair[1]);
        });
    }

    var nameQueued = false;
    var namer = new MutationObserver(function() {
        if (nameQueued) return;
        nameQueued = true;
        requestAnimationFrame(function() {
            nameQueued = false;
            nameControls();
        });
    });

    function startNamer() {
        if (!document.body) { setTimeout(startNamer, 100); return; }
        namer.observe(document.body, { childList: true, subtree: true });
        nameControls();
        applyMobilePlaceholder();
    }
    startNamer();

    // 7. Real-time submit button state sync & welcome chip click handlers
    function syncSubmitBtn() {
        var ta = document.querySelector('#chat-input textarea');
        var btn = document.getElementById('submit-btn');
        if (!ta || !btn) return;
        var hasText = (ta.value && ta.value.trim().length > 0);
        btn.style.opacity = hasText ? '1' : '0.5';
        btn.style.pointerEvents = hasText ? 'auto' : 'none';
    }

    document.addEventListener('input', function(e) {
        if (e.target && e.target.matches('#chat-input textarea')) {
            syncSubmitBtn();
        }
    });

    // Initialize once Gradio renders #sidebar & #plus-menu-container
    var attempts = 0;
    var timer = setInterval(function() {
        attempts++;
        var r1 = initResizer();
        var r2 = setupPlusMenu();
        syncSubmitBtn();
        if ((r1 && r2) || attempts >= 30) {
            clearInterval(timer);
        }
    }, 100);
})();
