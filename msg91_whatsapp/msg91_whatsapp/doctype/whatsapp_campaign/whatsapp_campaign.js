frappe.ui.form.on("WhatsApp Campaign", {
    refresh(frm) {
        render_timeline(frm);
        if (frm.is_new()) return;

        if (frm.doc.status === "Draft" || frm.doc.status === "Completed") {
            frm.add_custom_button(__("Start Campaign"), () => confirm_start(frm)).addClass(
                "btn-primary"
            );
        }

        if (frm.doc.status === "Active") {
            frm.add_custom_button(__("Pause"), () => run(frm, "pause"));
        }

        if (frm.doc.status === "Paused") {
            frm.add_custom_button(__("Resume"), () => run(frm, "resume"));
        }

        frm.add_custom_button(__("Send Test Message"), () => test_send(frm));

        frm.add_custom_button(__("Enrol a Lead"), () => enrol_lead(frm), __("Audience"));

        if (frm.doc.enroll_mode === "Saved Filter") {
            frm.add_custom_button(__("Preview Audience"), () => preview(frm), __("Audience"));
        }

        frm.add_custom_button(__("View Enrollments"), () => {
            frappe.set_route("List", "WhatsApp Campaign Enrollment", { campaign: frm.doc.name });
        });
    },
});

frappe.ui.form.on("WhatsApp Campaign Step", {
    steps_add: render_timeline,
    steps_remove: render_timeline,
    delay_hours: render_timeline,
    step_name: render_timeline,
    template: render_timeline,
    send_if: render_timeline,
    enabled: render_timeline,
});

function render_timeline(frm) {
    const wrapper = frm.get_field("journey_html");
    if (!wrapper) return;

    const intro = `<p class="text-muted small">${__(
        "Steps run in order. Each waits the given number of hours after the previous one, then sends unless its condition says otherwise."
    )}</p>`;

    const steps = (frm.doc.steps || []).filter((s) => s.enabled);
    if (!steps.length) {
        wrapper.$wrapper.html(
            intro +
                `<p class="text-muted small">${__(
                    "No nudges yet. The outreach template goes out on enrolment and the journey ends there."
                )}</p>`
        );
        return;
    }

    let offset = 0;
    const rows = steps
        .map((step, i) => {
            offset += step.delay_hours || 0;
            const when =
                offset < 48
                    ? __("{0}h after enrolment", [offset])
                    : __("day {0}", [+(offset / 24).toFixed(1)]);
            return `<tr>
                <td>${frappe.utils.escape_html(step.step_name || __("Step {0}", [i + 1]))}</td>
                <td class="text-muted">${when}</td>
                <td>${frappe.utils.escape_html(step.template || "-")}</td>
                <td class="text-muted">${frappe.utils.escape_html(step.send_if || "Always")}</td>
            </tr>`;
        })
        .join("");

    wrapper.$wrapper.html(`
        ${intro}
        <table class="table table-bordered" style="margin-bottom: 10px">
            <thead><tr>
                <th>${__("Step")}</th><th>${__("Lands")}</th>
                <th>${__("Template")}</th><th>${__("Only if")}</th>
            </tr></thead>
            <tbody>
                <tr>
                    <td><b>${__("First touch")}</b></td>
                    <td class="text-muted">${__("immediately")}</td>
                    <td>${frappe.utils.escape_html(frm.doc.outreach_template || "-")}</td>
                    <td class="text-muted">-</td>
                </tr>
                ${rows}
            </tbody>
        </table>
    `);
}

function test_send(frm) {
    if (frm.is_dirty()) {
        frappe.msgprint(__("Please save the campaign before sending a test."));
        return;
    }

    const dialog = new frappe.ui.Dialog({
        title: __("Send Test Message"),
        fields: [
            {
                fieldname: "info",
                fieldtype: "HTML",
                options: `<p class="text-muted small">${__(
                    "Sends the outreach template to this lead's number, using their real details. No enrolment is created and the campaign's counts are untouched."
                )}</p>`,
            },
            {
                fieldname: "lead",
                fieldtype: "Link",
                label: __("Send To Lead"),
                options: "CRM Lead",
                reqd: 1,
            },
        ],
        primary_action_label: __("Send"),
        primary_action(values) {
            frappe.call({
                doc: frm.doc,
                method: "test_send",
                args: { lead: values.lead },
                freeze: true,
                freeze_message: __("Sending ..."),
                callback: (r) => {
                    dialog.hide();
                    frappe.msgprint({
                        title: __("Test sent"),
                        indicator: "green",
                        message: __("Sent to {0}. Check WhatsApp Funnel Event for delivery.", [
                            r.message,
                        ]),
                    });
                },
            });
        },
    });
    dialog.show();
}

function confirm_start(frm) {
    if (frm.is_dirty()) {
        frappe.msgprint(__("Please save the campaign before starting it."));
        return;
    }

    frappe.confirm(
        __(
            "Starting sends the outreach template to everyone enrolled, and begins the nudge sequence. Continue?"
        ),
        () => {
            frappe.call({
                doc: frm.doc,
                method: "start",
                freeze: true,
                freeze_message: __("Starting campaign ..."),
                callback: (r) => {
                    const { enrolled = 0, queued = 0 } = r.message || {};
                    frappe.msgprint({
                        title: __("Campaign started"),
                        indicator: "green",
                        message: __("Enrolled {0} lead(s). {1} waiting for the first message.", [
                            enrolled,
                            queued,
                        ]),
                    });
                    frm.reload_doc();
                },
            });
        }
    );
}

function run(frm, method) {
    frappe.call({
        doc: frm.doc,
        method,
        freeze: true,
        callback: () => frm.reload_doc(),
    });
}

function enrol_lead(frm) {
    const dialog = new frappe.ui.Dialog({
        title: __("Enrol a Lead"),
        fields: [
            {
                fieldname: "lead",
                fieldtype: "Link",
                label: __("Lead"),
                options: "CRM Lead",
                reqd: 1,
            },
        ],
        primary_action_label: __("Enrol"),
        primary_action(values) {
            frappe.call({
                doc: frm.doc,
                method: "enroll_lead",
                args: { lead: values.lead },
                freeze: true,
                callback: () => {
                    dialog.hide();
                    frappe.show_alert({ message: __("Enrolled"), indicator: "green" });
                    frm.reload_doc();
                },
            });
        },
    });
    dialog.show();
}

function preview(frm) {
    frappe.call({
        doc: frm.doc,
        method: "preview_audience",
        freeze: true,
        callback: (r) => {
            frappe.msgprint({
                title: __("Audience"),
                message: __("{0} lead(s) currently match this filter.", [
                    (r.message && r.message.count) || 0,
                ]),
            });
        },
    });
}
