frappe.ui.form.on("WhatsApp Campaign", {
    refresh(frm) {
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

        frm.add_custom_button(__("Enrol a Lead"), () => enrol_lead(frm), __("Audience"));

        if (frm.doc.enroll_mode === "Saved Filter") {
            frm.add_custom_button(__("Preview Audience"), () => preview(frm), __("Audience"));
        }

        frm.add_custom_button(__("View Enrollments"), () => {
            frappe.set_route("List", "WhatsApp Campaign Enrollment", { campaign: frm.doc.name });
        });
    },
});

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
