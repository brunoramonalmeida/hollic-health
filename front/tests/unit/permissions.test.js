// Unit tests for the role permissions the UI mirrors from the API.
// Runs with Node's built-in runner: `npm run test:unit`.
import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { ROLE_LABEL, STAFF_ROLES, canSign, permissionsFor } from '../../src/permissions.js'

const user = (role, extra = {}) => ({ id: 1, name: 'X', email: 'x@example.com', role, ...extra })

describe('permissionsFor', () => {
  it('knows every role of the API, including the professional', () => {
    assert.deepEqual(Object.keys(ROLE_LABEL), ['admin', 'supervisor', 'attendant', 'client', 'professional', 'seller', 'partner'])
    assert.equal(ROLE_LABEL.partner, 'Partner company')
    assert.equal(ROLE_LABEL.professional, 'Professional')
    assert.equal(ROLE_LABEL.seller, 'Seller')
    assert.deepEqual(STAFF_ROLES, ['admin', 'supervisor', 'attendant'])
  })

  it('gives the professional its own area and nothing of the staff', () => {
    const p = permissionsFor(user('professional', { professional_id: 7 }))
    assert.equal(p.isProfessional, true)
    assert.equal(p.isStaff, false)
    assert.equal(p.isClient, false)
    assert.equal(p.professionalId, 7)
    assert.equal(p.customerId, null)
    assert.equal(p.canSignAppointments, true)
    assert.equal(p.canSeeOwnPayouts, true)
    assert.equal(p.canBookAppointments, false)
    assert.equal(p.canDeleteAppointments, false)
    assert.equal(p.canManageCustomers, false)
    assert.equal(p.canManageUsers, false)
    assert.equal(p.canEditPricing, false)
    assert.equal(p.canSeeBalanceReports, false)
    assert.equal(p.canSeeAttendantReport, false)
  })

  it('gives the seller its own area: sales, portfolio, region, commissions, account — and nothing else', () => {
    const p = permissionsFor(user('seller', { seller_id: 5 }))
    assert.equal(p.isSeller, true)
    assert.equal(p.sellerId, 5)
    assert.equal(p.isStaff, false)
    assert.equal(p.isClient, false)
    assert.equal(p.canSeeOwnSales, true)
    assert.equal(p.canSellPlans, true)
    assert.equal(p.canSeeSubscriptions, true)
    assert.equal(p.canManageCustomers, true)
    assert.equal(p.canDeleteCustomers, false)
    assert.equal(p.canEditSubscriptions, false)
    assert.equal(p.canRequestSaleChanges, true)
    assert.equal(p.canDecideSaleChanges, false)
    assert.equal(p.canRegisterPayments, false)
    assert.equal(p.canBookAppointments, false)
    assert.equal(p.canSeeSales, false)
    assert.equal(p.canManageSales, false)
    assert.equal(p.canRecordSellerSales, false)
    assert.equal(p.canSeeCommissions, false)
    assert.equal(p.canSeeBalanceReports, false)
    assert.equal(p.canManageUsers, false)
    assert.equal(p.canEditPricing, false)
    assert.equal(p.canSignAppointments, false)
  })

  it('keeps the seller area for sellers only', () => {
    for (const role of ['admin', 'supervisor', 'attendant', 'client', 'professional']) {
      const p = permissionsFor(user(role, { customer_id: role === 'client' ? 3 : null, professional_id: role === 'professional' ? 1 : null }))
      assert.equal(p.isSeller, false, role)
      assert.equal(p.canSeeOwnSales, false, role)
      assert.equal(p.sellerId, null, role)
    }
    assert.equal(permissionsFor(user('attendant')).canSellPlans, true)
    assert.equal(permissionsFor(user('client', { customer_id: 3 })).canSellPlans, false)
    assert.equal(permissionsFor(user('client', { customer_id: 3 })).canSeeSubscriptions, true)
    assert.equal(permissionsFor(user('client', { customer_id: 3 })).canDecideSaleChanges, true)
    assert.equal(permissionsFor(user('attendant')).canDecideSaleChanges, true)
    assert.equal(permissionsFor(user('attendant')).canRequestSaleChanges, false)
    assert.equal(permissionsFor(user('professional', { professional_id: 1 })).canSeeSubscriptions, false)
  })

  it('never lets another role sign or see own payouts', () => {
    for (const role of ['admin', 'supervisor', 'attendant', 'client', 'seller']) {
      const p = permissionsFor(user(role, { customer_id: role === 'client' ? 3 : null, seller_id: role === 'seller' ? 5 : null }))
      assert.equal(p.canSignAppointments, false, role)
      assert.equal(p.canSeeOwnPayouts, false, role)
      assert.equal(p.isProfessional, false, role)
      assert.equal(p.professionalId, null, role)
    }
  })

  it('lets staff and clients book, professionals not', () => {
    assert.equal(permissionsFor(user('admin')).canBookAppointments, true)
    assert.equal(permissionsFor(user('attendant')).canBookAppointments, true)
    assert.equal(permissionsFor(user('client', { customer_id: 3 })).canBookAppointments, true)
    assert.equal(permissionsFor(user('professional', { professional_id: 1 })).canBookAppointments, false)
  })

  it('keeps the staff rules unchanged', () => {
    const admin = permissionsFor(user('admin'))
    assert.equal(admin.isStaff, true)
    assert.equal(admin.canSeeBalanceReports, true)
    assert.equal(admin.canManageStaffUsers, true)
    const supervisor = permissionsFor(user('supervisor'))
    assert.equal(supervisor.canSeeBalanceReports, false)
    assert.equal(supervisor.canSeeAttendantReport, true)
    assert.equal(supervisor.canManageUsers, true)
    assert.equal(supervisor.canManageStaffUsers, false)
    const attendant = permissionsFor(user('attendant'))
    assert.equal(attendant.canEditPricing, false)
    assert.equal(attendant.canRegisterPayments, true)
    const client = permissionsFor(user('client', { customer_id: 9 }))
    assert.equal(client.isClient, true)
    assert.equal(client.customerId, 9)
    assert.equal(client.canDeleteAppointments, false)
  })

  it('mirrors the sales rules: staff read regions and sellers, managers edit, only admins see commissions', () => {
    const expected = {
      admin: { canSeeSales: true, canManageSales: true, canRecordSellerSales: true, canSeeCommissions: true },
      supervisor: { canSeeSales: true, canManageSales: true, canRecordSellerSales: true, canSeeCommissions: false },
      attendant: { canSeeSales: true, canManageSales: false, canRecordSellerSales: true, canSeeCommissions: false },
      client: { canSeeSales: false, canManageSales: false, canRecordSellerSales: false, canSeeCommissions: false },
      professional: { canSeeSales: false, canManageSales: false, canRecordSellerSales: false, canSeeCommissions: false },
    }
    for (const [role, flags] of Object.entries(expected)) {
      const p = permissionsFor(user(role, { customer_id: role === 'client' ? 3 : null, professional_id: role === 'professional' ? 1 : null }))
      for (const [flag, value] of Object.entries(flags)) assert.equal(p[flag], value, `${role}.${flag}`)
    }
  })

  it('lets the staff read partner companies, enroll employees and register invoices; admins and supervisors negotiate', () => {
    const expected = {
      admin: { canSeePartners: true, canManagePartners: true, canEnrollEmployees: true, canPayInvoices: true },
      supervisor: { canSeePartners: true, canManagePartners: true, canEnrollEmployees: true, canPayInvoices: true },
      attendant: { canSeePartners: true, canManagePartners: false, canEnrollEmployees: true, canPayInvoices: true },
      client: { canSeePartners: false, canManagePartners: false, canEnrollEmployees: false, canPayInvoices: false },
      professional: { canSeePartners: false, canManagePartners: false, canEnrollEmployees: false, canPayInvoices: false },
      seller: { canSeePartners: false, canManagePartners: false, canEnrollEmployees: false, canPayInvoices: false },
      partner: { canSeePartners: false, canManagePartners: false, canEnrollEmployees: false, canPayInvoices: false },
    }
    for (const [role, flags] of Object.entries(expected)) {
      const p = permissionsFor(user(role))
      for (const [flag, value] of Object.entries(flags)) assert.equal(p[flag], value, `${role}.${flag}`)
    }
  })

  it('gives a partner company its portal and nothing of the network', () => {
    const p = permissionsFor(user('partner', { partner_id: 4 }))
    assert.equal(p.isPartner, true)
    assert.equal(p.partnerId, 4)
    assert.equal(p.canManageOwnEmployees, true)
    for (const flag of [
      'isStaff', 'isClient', 'isSeller', 'isProfessional', 'canBookAppointments', 'canManageCustomers', 'canSeeSubscriptions',
      'canSellPlans', 'canEditSubscriptions', 'canSeePartners', 'canManagePartners', 'canEnrollEmployees', 'canPayInvoices',
      'canRegisterPayments', 'canManageUsers', 'canSeeBalanceReports', 'canEditPricing', 'canSeeSales',
    ]) {
      assert.equal(p[flag], false, flag)
    }
    assert.equal(permissionsFor(user('admin')).canManageOwnEmployees, false)
  })

  it('lets a client check its own payments and the staff read every statement', () => {
    assert.equal(permissionsFor(user('client', { customer_id: 3 })).canSeeOwnPayments, true)
    for (const role of ['admin', 'supervisor', 'attendant']) {
      assert.equal(permissionsFor(user(role)).canSeeCompanyStatements, true, role)
      assert.equal(permissionsFor(user(role)).canSeeOwnPayments, false, role)
    }
    for (const role of ['client', 'professional', 'seller', 'partner']) {
      assert.equal(permissionsFor(user(role)).canSeeCompanyStatements, false, role)
    }
  })

  it('handles a missing user (signed out)', () => {
    const p = permissionsFor(null)
    assert.equal(p.role, undefined)
    assert.equal(p.isStaff, false)
    assert.equal(p.isProfessional, false)
    assert.equal(p.canBookAppointments, false)
    assert.equal(p.canSeeSales, false)
    assert.equal(p.canManageSales, false)
    assert.equal(p.canSeePartners, false)
    assert.equal(p.canManagePartners, false)
  })
})

describe('canSign', () => {
  const professional = permissionsFor(user('professional', { professional_id: 1 }))
  const admin = permissionsFor(user('admin'))

  it('needs both the role and the API flag', () => {
    assert.equal(canSign(professional, { can_sign: true }), true)
    assert.equal(canSign(professional, { can_sign: false }), false)
    assert.equal(canSign(admin, { can_sign: true }), false)
    assert.equal(canSign(professional, null), false)
  })
})
