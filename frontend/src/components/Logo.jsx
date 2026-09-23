// A water drop whose inside is a small contour map: the product in one mark.
export function LogoMark({ size = 40 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true" focusable="false">
      <defs>
        <clipPath id="jd-drop">
          <path d="M24 3.5C24 3.5 40 20.6 40 31.2a16 16 0 0 1-32 0C8 20.6 24 3.5 24 3.5Z" />
        </clipPath>
      </defs>
      <path d="M24 3.5C24 3.5 40 20.6 40 31.2a16 16 0 0 1-32 0C8 20.6 24 3.5 24 3.5Z" fill="var(--water)" />
      <g clipPath="url(#jd-drop)" fill="none" stroke="#fff" strokeOpacity="0.85" strokeWidth="1.6">
        <path d="M2 27c6-3 10 2 16 0s9-6 15-4 9 4 13 2" />
        <path d="M2 33c7-2 11 3 17 1s10-5 15-3 9 3 12 2" />
        <path d="M2 39c7-1 12 2 18 1s10-3 15-2 8 2 11 1" />
      </g>
      <ellipse cx="24" cy="36.5" rx="6.5" ry="2.4" fill="var(--parcel)" />
    </svg>
  )
}

export default function Logo() {
  return (
    <div className="brand">
      <LogoMark />
      <div>
        <div className="brand-name">
          JalDrishti <span lang="hi">जलदृष्टि</span>
        </div>
        <div className="brand-sub">Village pond &amp; catchment planner</div>
      </div>
    </div>
  )
}
