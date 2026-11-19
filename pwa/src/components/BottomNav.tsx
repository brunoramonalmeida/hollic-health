import { NavLink } from 'react-router-dom'

const tabs = [
  {
    to: '/app',
    end: true,
    label: 'HOME',
    icon: '/assets/icons/home_icon.svg',
    activeIcon: '/assets/icons/home_icon_filled.svg',
  },
  {
    to: '/app/appointments',
    label: 'APPOINTMENTS',
    icon: '/assets/icons/appointments_icon.svg',
    activeIcon: '/assets/icons/appointments_icon_filled.svg',
  },
  {
    to: '/app/profile',
    label: 'PROFILE',
    icon: '/assets/icons/profile_icon.svg',
    activeIcon: '/assets/icons/profile_icon_filled.svg',
  },
]

export function BottomNav() {
  return (
    <nav className="bottom-nav">
      {tabs.map((tab) => (
        <NavLink
          key={tab.to}
          to={tab.to}
          end={tab.end}
          className={({ isActive }) => `bottom-nav__item ${isActive ? 'is-active' : ''}`}
        >
          {({ isActive }) => (
            <>
              <img src={isActive ? tab.activeIcon : tab.icon} alt="" />
              <span>{tab.label}</span>
            </>
          )}
        </NavLink>
      ))}
    </nav>
  )
}
