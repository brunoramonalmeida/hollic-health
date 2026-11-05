import { Outlet } from 'react-router-dom'
import { AppBar } from '../components/AppBar'
import { BottomNav } from '../components/BottomNav'

export function HomeBase() {
  return (
    <div className="home-layout">
      <AppBar />
      <div className="home-layout__content">
        <Outlet />
      </div>
      <BottomNav />
    </div>
  )
}
