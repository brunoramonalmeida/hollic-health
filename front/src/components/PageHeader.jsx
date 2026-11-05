import { Link } from 'react-router-dom'

/** Title block used by every list and form page. `back` renders a return link. */
export default function PageHeader({ crumb, title, hint, back, children }) {
  return (
    <header className="page-head">
      <div>
        <p className="crumb">
          {back ? <Link to={back.to}>← {back.label}</Link> : crumb}
        </p>
        <h1>{title}</h1>
        {hint && <p className="muted">{hint}</p>}
      </div>
      {children && <div className="toolbar">{children}</div>}
    </header>
  )
}
