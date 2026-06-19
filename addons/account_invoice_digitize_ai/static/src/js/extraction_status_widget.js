/** @odoo-module */

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, onMounted, onWillUnmount, onPatched } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

const POLL_INTERVAL_MS = 5000;

class AiExtractionStatusWidget extends Component {
    static template = "account_invoice_digitize_ai.AiExtractionStatusWidget";
    static props = { ...standardFieldProps };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this._intervalId = null;

        onMounted(() => this._checkAndStartPolling());
        onPatched(() => this._checkAndStartPolling());
        onWillUnmount(() => this._stopPolling());
    }

    get currentStatus() {
        return this.props.record.data[this.props.name] || false;
    }

    get isProcessing() {
        return this.currentStatus === "processing";
    }

    get isDone() {
        return this.currentStatus === "done";
    }

    get isFailed() {
        return this.currentStatus === "failed";
    }

    _checkAndStartPolling() {
        if (this.isProcessing && !this._intervalId) {
            this._startPolling();
        } else if (!this.isProcessing && this._intervalId) {
            this._stopPolling();
        }
    }

    _startPolling() {
        this._intervalId = setInterval(() => this._poll(), POLL_INTERVAL_MS);
    }

    _stopPolling() {
        if (this._intervalId) {
            clearInterval(this._intervalId);
            this._intervalId = null;
        }
    }

    async _poll() {
        const recordId = this.props.record.resId;
        if (!recordId) return;

        try {
            const result = await this.orm.read(
                "account.move",
                [recordId],
                ["ai_extraction_status"],
            );
            if (!result || !result.length) return;

            const newStatus = result[0].ai_extraction_status;

            if (newStatus === "done") {
                this._stopPolling();
                this.notification.add(
                    "AI extraction complete. Opening results...",
                    { type: "success" },
                );
                // Trigger view results action
                await this.action.doActionButton({
                    type: "object",
                    name: "action_ai_view_results",
                    resModel: "account.move",
                    resId: recordId,
                    resIds: [recordId],
                });
            } else if (newStatus === "failed") {
                this._stopPolling();
                this.notification.add(
                    "AI extraction failed. Please try again.",
                    { type: "danger" },
                );
                // Reload to show updated status
                await this.props.record.load();
            }
        } catch (error) {
            console.warn("Extraction polling error:", error);
        }
    }
}

const aiExtractionStatusField = {
    component: AiExtractionStatusWidget,
    supportedTypes: ["selection", "char"],
    isEmpty: () => false,
};

registry.category("fields").add("ai_extraction_status", aiExtractionStatusField);
