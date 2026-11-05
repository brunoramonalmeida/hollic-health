import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  CHeader,
  CNavItem,
  CNavTitle,
  CSidebar,
  CSidebarBrand,
  CSidebarNav,
} from '@coreui/react'
import CIcon from '@coreui/icons-react'
import {
  cilAccountLogout,
  cilBell,
  cilBriefcase,
  cilBuilding,
  cilCalendar,
  cilChart,
  cilCreditCard,
  cilContact,
  cilDescription,
  cilHome,
  cilIndustry,
  cilHospital,
  cilMap,
  cilMoney,
  cilMedicalCross,
  cilNotes,
  cilPeople,
  cilSettings,
  cilSpeedometer,
  cilUser,
} from '@coreui/icons'
import NotificationBell from '../components/NotificationBell'
import { ROLE_LABEL, useSession } from '../session'

const ICONS = {
  '/': cilSpeedometer,
  '/appointments': cilCalendar,
  '/customers': cilPeople,
  '/subscriptions': cilCreditCard,
  '/professionals': cilUser,
  '/clinics': cilHospital,
  '/specialties': cilMedicalCross,
  '/modalities': cilNotes,
  '/plan-categories': cilNotes,
  '/tax-rules': cilDescription,
  '/company': cilBuilding,
  '/reports': cilChart,
  '/users': cilPeople,
  '/queue': cilBell,
  '/my-payouts': cilChart,
  '/payouts': cilCreditCard,
  '/regions': cilMap,
  '/sellers': cilBriefcase,
  '/partners': cilIndustry,
  '/agreements': cilContact,
}

/** Navigation by role. Each link only shows when the role can open the page. */
function navGroups(session) {
  if (session.isClient) {
    return [
      {
        title: 'My area',
        links: [
          ['/', 'Home', cilHome],
          ['/appointments', 'My appointments'],
          ['/subscriptions', 'My plan'],
          ['/my-payments', 'My payments', cilMoney],
          ['/my-card', 'My virtual card', cilCreditCard],
        ],
      },
    ]
  }
  if (session.isSeller) {
    const links = [
      ['/', 'Home', cilHome],
      ['/subscriptions', 'My sales', cilCreditCard],
      ['/customers', 'My customers', cilPeople],
      ['/my-region', 'My region', cilMap],
      ['/my-commissions', 'My commissions', cilMoney],
      ['/my-account', 'My account', cilUser],
    ]
    // Coordinators: the sellers reporting to them and the override they earn.
    if (session.user?.seller_team_size > 0) links.splice(3, 0, ['/my-team', 'My team', cilPeople])
    return [{ title: 'My area', links }]
  }
  if (session.isPartner) {
    // The company portal: its employees, its agreements and invoices — nothing else of the network.
    return [
      {
        title: 'Company portal',
        links: [
          ['/', 'Home', cilHome],
          ['/my-employees', 'Employees', cilPeople],
          ['/my-agreements', 'Agreements', cilContact],
          ['/my-statement', 'Statement', cilMoney],
        ],
      },
    ]
  }
  if (session.isProfessional) {
    return [
      {
        title: 'My area',
        links: [
          ['/', 'Home', cilHome],
          ['/my-agenda', 'My agenda', cilCalendar],
          ['/appointments', 'My appointments'],
          ['/my-payouts', 'My payouts'],
          ['/my-profile', 'My profile', cilUser],
          ['/my-security', 'Security', cilSettings],
        ],
      },
    ]
  }
  const care = [['/appointments', 'Appointments'], ['/customers', 'Customers'], ['/subscriptions', 'Subscriptions']]
  const records = [['/professionals', 'Professionals'], ['/clinics', 'Clinics'], ['/specialties', 'Specialties'], ['/modalities', 'Plans'], ['/plan-categories', 'Plan categories']]
  const management = []
  if (session.canSeeBalanceReports || session.canSeeAttendantReport) management.push(['/reports', 'Reports'])
  if (session.canManageUsers) management.push(['/users', 'Users'])
  if (session.role === 'admin') management.push(['/queue', 'Delivery queue'])
  const sales = [['/regions', 'Regions'], ['/sellers', 'Sellers']]
  // Corporate partnerships (B2B): plans negotiated with companies for their employees.
  const corporate = [['/partners', 'Partner companies'], ['/agreements', 'Agreements']]
  const finance = [['/tax-rules', 'Tax rules'], ['/company', 'Company']]
  if (session.canManagePayouts) finance.unshift(['/payouts', 'Payouts'])
  const groups = [
    { title: 'Overview', links: [['/', 'Dashboard']] },
    { title: 'Care', links: care },
    { title: 'Records', links: records },
  ]
  if (session.canSeeSales) groups.push({ title: 'Sales', links: sales })
  if (session.canSeePartners) groups.push({ title: 'Corporate', links: corporate })
  groups.push({ title: 'Finance', links: finance })
  if (management.length) groups.push({ title: 'Management', links: management })
  return groups
}

export default function Shell() {
  const navigate = useNavigate()
  const session = useSession()
  const user = session.user

  function exit() {
    localStorage.removeItem('token')
    session.refresh()
    navigate('/login')
  }

  const initials = (user?.name || 'SS')
    .split(' ')
    .slice(0, 2)
    .map((part) => part[0])
    .join('')
    .toUpperCase()

  return (
    <div>
      <CSidebar position="fixed" visible>
        <CSidebarBrand className="brand-block">
          <span className="brand-mark">S</span>
          <p className="brand-name">Santé Saúde<small>Operations</small></p>
        </CSidebarBrand>
        <CSidebarNav>
          {navGroups(session).map((group) => (
            <div key={group.title}>
              <CNavTitle>{group.title}</CNavTitle>
              {group.links.map(([to, label, icon]) => (
                <CNavItem key={to}>
                  <NavLink className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`} to={to} end={to === '/'}>
                    <CIcon customClassName="nav-icon" icon={icon || ICONS[to] || cilSettings} />
                    {label}
                  </NavLink>
                </CNavItem>
              ))}
            </div>
          ))}
        </CSidebarNav>
        <div className="account">
          <span className="avatar">{initials}</span>
          <div>
            <strong>{user?.name || 'Staff'}</strong>
            <small>{user?.email || ''}</small>
          </div>
        </div>
      </CSidebar>
      <div className="wrapper d-flex flex-column min-vh-100">
        <CHeader position="sticky" className="topbar">
          <strong>{session.isClient ? 'Client area' : session.isSeller ? 'Sales area' : session.isPartner ? 'Company portal' : 'Operations'}</strong>
          <div className="topbar-right">
            <span>
              {ROLE_LABEL[user?.role] || 'Session'}
              {session.isClient && user?.customer_name ? ` · ${user.customer_name}` : ''}
              {session.isSeller && user?.seller_name ? ` · ${user.seller_name}` : ''}
              {session.isPartner && user?.partner_name ? ` · ${user.partner_name}` : ''}
            </span>
            <NotificationBell />
            <button type="button" className="sign-out" onClick={exit} title="Sign out">
              <CIcon icon={cilAccountLogout} className="sign-out-icon" />
              Sign out
            </button>
          </div>
        </CHeader>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
