/** @odoo-module */

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

class AiConfidenceWidget extends Component {
    static template = "account_invoice_digitize_ai.AiConfidenceWidget";
    static props = { ...standardFieldProps };

    get confidenceData() {
        const raw = this.props.record.data[this.props.name];
        if (!raw) return {};
        try {
            return JSON.parse(raw);
        } catch {
            return {};
        }
    }

    static CONFIDENCE_FIELDS = [
        "partner_id",
        "ref",
        "invoice_date",
        "invoice_date_due",
        "totals",
    ];

    get overallScore() {
        const data = this.confidenceData;
        const values = AiConfidenceWidget.CONFIDENCE_FIELDS.map((f) => data[f]).filter(
            (v) => typeof v === "number",
        );
        if (!values.length) return null;
        return values.reduce((a, b) => a + b, 0) / values.length;
    }

    get hasScore() {
        return this.overallScore !== null;
    }

    get scorePercent() {
        const score = this.overallScore;
        return score !== null ? Math.round(score * 100) : 0;
    }

    get badgeClass() {
        const score = this.overallScore;
        if (score === null) return "";
        if (score >= 0.8) return "text-bg-success";
        if (score >= 0.5) return "text-bg-warning";
        return "text-bg-danger";
    }

    get titleText() {
        return _t("%s%% extraction confidence", this.scorePercent);
    }

    get iconClass() {
        const score = this.overallScore;
        if (score === null) return "";
        if (score >= 0.8) return "fa-check-circle";
        if (score >= 0.5) return "fa-exclamation-circle";
        return "fa-times-circle";
    }
}

const aiConfidenceField = {
    component: AiConfidenceWidget,
    supportedTypes: ["text", "char"],
    isEmpty: () => false,
};

registry.category("fields").add("ai_confidence", aiConfidenceField);
