"""Isolation settings for the project's existing synthetic beta preview Site."""
import frappe


def configure():
    if frappe.local.site!='dsherp-beta.localhost':frappe.throw('仅配置本项目合成预览站点')
    if frappe.db.count('Webhook') or frappe.db.count('Email Account'):
        frappe.throw('预览站点已有外部连接，须先核实并移除')
    from frappe.installer import update_site_config
    for key in ('dsherp_preview','mute_emails','disable_scheduler','pause_scheduler'):
        update_site_config(key,1)
    frappe.clear_cache()


def boot(bootinfo):
    if frappe.conf.get('dsherp_preview'):bootinfo.disable_async=True


def prevent_external_configuration(doc,method=None):
    if frappe.conf.get('dsherp_preview'):
        frappe.throw('隔离预览不允许外部副作用配置')
