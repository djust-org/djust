
        // ---- Dockable panel: dock position + resize ----
        //
        // The panel used to be a hardcoded full-width bottom dock, which
        // covers bottom-anchored app UI (chat inputs, sticky footers).
        // It can now dock to the bottom, left, or right edge and be
        // resized by dragging its inner edge. Both preferences persist
        // per view via saveState()/loadState().

        _dockPositions() {
            return ['bottom', 'left', 'right'];
        }

        _clampDockSizes() {
            const vw = window.innerWidth || 1024;
            const vh = window.innerHeight || 768;
            const minHeight = 160;
            const minWidth = 320;
            const maxHeight = Math.max(minHeight, Math.round(vh * 0.9));
            const maxWidth = Math.max(minWidth, Math.round(vw * 0.9));
            this.state.panelHeight = Math.min(Math.max(this.state.panelHeight || 400, minHeight), maxHeight);
            this.state.panelWidth = Math.min(Math.max(this.state.panelWidth || 480, minWidth), maxWidth);
        }

        setDock(position) {
            if (!this._dockPositions().includes(position)) return;
            this.state.dock = position;
            if (this.state.isOpen) {
                this._applyDockStyles();
            } else {
                this._updateDockButtons();
                this._positionResizeHandle();
            }
            this.saveState();
        }

        _applyDockStyles() {
            this._clampDockSizes();
            const dock = this.state.dock;
            const base =
                'display: flex !important;' +
                'position: fixed !important;' +
                'background: #0f172a !important;' +
                'color: #f1f5f9 !important;' +
                'z-index: 999999 !important;';
            let style;
            if (dock === 'left') {
                style = base +
                    `top: 0 !important; bottom: 0 !important; left: 0 !important; right: auto !important;` +
                    `width: ${this.state.panelWidth}px !important; height: 100% !important;` +
                    'border-right: 2px solid #E57324 !important;' +
                    'box-shadow: 4px 0 12px rgba(0, 0, 0, 0.5) !important;';
            } else if (dock === 'right') {
                style = base +
                    `top: 0 !important; bottom: 0 !important; right: 0 !important; left: auto !important;` +
                    `width: ${this.state.panelWidth}px !important; height: 100% !important;` +
                    'border-left: 2px solid #E57324 !important;' +
                    'box-shadow: -4px 0 12px rgba(0, 0, 0, 0.5) !important;';
            } else {
                style = base +
                    `bottom: 0 !important; left: 0 !important; right: 0 !important; top: auto !important;` +
                    `width: 100% !important; height: ${this.state.panelHeight}px !important;` +
                    'border-top: 2px solid #E57324 !important;' +
                    'box-shadow: 0 -4px 12px rgba(0, 0, 0, 0.5) !important;';
            }
            this.panel.setAttribute('style', style);
            this._positionResizeHandle();
            this._updateDockButtons();
            this._updateButtonOffset();
        }

        _updateDockButtons() {
            if (!this.panel) return;
            this.panel.querySelectorAll('.djust-btn-dock').forEach((btn) => {
                btn.classList.toggle('active', btn.dataset.dock === this.state.dock);
            });
        }

        // Keep the floating toggle button visible next to the open panel
        // instead of underneath it.
        _updateButtonOffset() {
            if (!this.button) return;
            if (!this.state.isOpen) {
                this.button.style.bottom = '';
                this.button.style.right = '';
                return;
            }
            if (this.state.dock === 'bottom') {
                this.button.style.bottom = `${this.state.panelHeight + 16}px`;
                this.button.style.right = '';
            } else if (this.state.dock === 'right') {
                this.button.style.right = `${this.state.panelWidth + 16}px`;
                this.button.style.bottom = '';
            } else {
                this.button.style.bottom = '';
                this.button.style.right = '';
            }
        }

        _initDockUI() {
            this.panel.querySelectorAll('.djust-btn-dock').forEach((btn) => {
                btn.addEventListener('click', () => this.setDock(btn.dataset.dock));
            });
            this._createResizeHandle();
            this._updateDockButtons();
        }

        _createResizeHandle() {
            this.resizeHandle = document.createElement('div');
            this.resizeHandle.className = 'djust-resize-handle';
            this.resizeHandle.addEventListener('mousedown', (e) => this._startResize(e));
            this.panel.appendChild(this.resizeHandle);
            this._positionResizeHandle();
        }

        _positionResizeHandle() {
            if (!this.resizeHandle) return;
            const dock = this.state.dock;
            // The handle sits on the panel edge that faces the page:
            // top edge for a bottom dock, left edge for a right dock,
            // right edge for a left dock.
            this.resizeHandle.classList.toggle('djust-resize-ns', dock === 'bottom');
            this.resizeHandle.classList.toggle('djust-resize-ew-left', dock === 'right');
            this.resizeHandle.classList.toggle('djust-resize-ew-right', dock === 'left');
        }

        _startResize(e) {
            e.preventDefault();
            const dock = this.state.dock;
            const startX = e.clientX;
            const startY = e.clientY;
            const startHeight = this.state.panelHeight;
            const startWidth = this.state.panelWidth;
            this.resizeHandle.classList.add('dragging');

            const onMove = (ev) => {
                if (dock === 'bottom') {
                    this.state.panelHeight = startHeight + (startY - ev.clientY);
                } else if (dock === 'right') {
                    this.state.panelWidth = startWidth + (startX - ev.clientX);
                } else {
                    this.state.panelWidth = startWidth + (ev.clientX - startX);
                }
                // Re-applies styles with clamped sizes
                this._applyDockStyles();
            };
            const onUp = () => {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                this.resizeHandle.classList.remove('dragging');
                this.saveState();
            };
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        }
