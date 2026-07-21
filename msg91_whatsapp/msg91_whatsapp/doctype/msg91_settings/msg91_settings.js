frappe.ui.form.on("MSG91 Settings", {
    refresh(frm) {
        frm.add_custom_button(__("Send Test (free-form)"), () => {
            if (frm.is_dirty()) {
                frappe.msgprint(__("Please save the settings before testing."));
                return;
            }
            frappe.call({
                doc: frm.doc,
                method: "send_test",
                freeze: true,
                freeze_message: __("Sending via MSG91 ..."),
                callback: (r) => {
                    frappe.msgprint({
                        title: __("MSG91 Response"),
                        indicator: "green",
                        message: "<pre>" +
                            frappe.utils.escape_html(JSON.stringify(r.message, null, 2)) +
                            "</pre>",
                    });
                },
            });
        });
    },
});
