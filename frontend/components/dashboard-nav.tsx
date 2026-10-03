import Link from "next/link";

const links = [
  ["Overview", "/overview"],
  ["Upload FIT/TCX", "/upload"],
  ["Progress", "/progress"],
  ["FTP calibration", "/ftp-calibration"],
  ["Activities", "/activities"],
  ["Power curve", "/power-curve"],
  ["Heart rate", "/heart-rate"],
  ["Durability", "/durability"],
  ["Load", "/load"],
  ["Calendar", "/calendar"],
  ["Settings", "/settings"],
] as const;

export function DashboardNav() {
  return <nav className="nav" aria-label="Dashboard">{links.map(([name, href]) => <Link key={href} href={href}>{name}</Link>)}</nav>;
}
