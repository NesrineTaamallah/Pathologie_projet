

const base = (size, color, children) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke={color}
    strokeWidth="1.6"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    {children}
  </svg>
);

export function IconShield({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3z" />
  ));
}

export function IconChart({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <path d="M4 19V9M10 19V5M16 19v-7M22 19H2" />
    </>
  ));
}

export function IconWave({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <path d="M2 12h4l2-6 3 12 2-9 2 6h4l2-3h3" />
  ));
}

export function IconUsers({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <circle cx="8.5" cy="8" r="3" />
      <path d="M2.5 20c0-3.3 2.7-6 6-6s6 2.7 6 6" />
      <circle cx="17" cy="8.5" r="2.4" />
      <path d="M15.5 12.2c2.3.4 4 2.3 4 4.6v2.2" />
    </>
  ));
}

export function IconSearch({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="M20 20l-4.8-4.8" />
    </>
  ));
}

export function IconLock({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <rect x="4.5" y="10.5" width="15" height="10" rx="1.5" />
      <path d="M7.5 10.5V7a4.5 4.5 0 0 1 9 0v3.5" />
    </>
  ));
}

export function IconArrowRight({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <path d="M4 12h16M13 5l7 7-7 7" />
  ));
}

export function IconArrowLeft({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <path d="M20 12H4M11 19l-7-7 7-7" />
  ));
}

export function IconEye({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <path d="M2 12s3.8-7 10-7 10 7 10 7-3.8 7-10 7-10-7-10-7z" />
      <circle cx="12" cy="12" r="2.6" />
    </>
  ));
}

export function IconEyeOff({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <path d="M3 3l18 18" />
      <path d="M10.6 5.2A10.6 10.6 0 0 1 12 5c6.2 0 10 7 10 7a17.7 17.7 0 0 1-3.4 4.3M6.3 6.3A17.6 17.6 0 0 0 2 12s3.8 7 10 7c1.3 0 2.5-.2 3.6-.6" />
      <path d="M9.9 10.1a2.6 2.6 0 0 0 3.7 3.7" />
    </>
  ));
}

export function IconCheckCircle({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 12.3l2.3 2.3 4.7-5" />
    </>
  ));
}

export function IconAlert({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <path d="M12 3.5l9.5 16.5H2.5L12 3.5z" />
      <path d="M12 10v4.2" />
      <circle cx="12" cy="17.2" r="0.4" fill={color} stroke="none" />
    </>
  ));
}

export function IconFolder({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.6l1.6 2H19.5A1.5 1.5 0 0 1 21 8.5v9A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5v-11z" />
  ));
}

export function IconHeart({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <path d="M12 20.5s-7.5-4.6-9.8-9.3C.8 7.8 2.6 4.5 6 4c2-.3 3.7.7 6 3 2.3-2.3 4-3.3 6-3 3.4.5 5.2 3.8 3.8 7.2C19.5 15.9 12 20.5 12 20.5z" />
  ));
}

export function IconPlus({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <path d="M12 5v14M5 12h14" />
  ));
}

export function IconX({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <path d="M6 6l12 12M18 6L6 18" />
  ));
}

export function IconLogout({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <path d="M9 4H5.5A1.5 1.5 0 0 0 4 5.5v13A1.5 1.5 0 0 0 5.5 20H9" />
      <path d="M14 16l5-4-5-4M19 12H9" />
    </>
  ));
}

export function IconDots({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <circle cx="12" cy="5.5" r="1.1" fill={color} stroke="none" />
      <circle cx="12" cy="12" r="1.1" fill={color} stroke="none" />
      <circle cx="12" cy="18.5" r="1.1" fill={color} stroke="none" />
    </>
  ));
}

export function IconHistory({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <path d="M3 11a9 9 0 1 1 2.6 6.4" />
      <path d="M3 5v6h6" />
      <path d="M12 8v4.5l3 2" />
    </>
  ));
}

export function IconMail({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <rect x="3" y="5.5" width="18" height="13" rx="2" />
      <path d="M3.5 6.5l8.5 6.5 8.5-6.5" />
    </>
  ));
}

export function IconUnlock({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <rect x="4.5" y="11" width="15" height="9.5" rx="2" />
      <path d="M8 11V7.5a4 4 0 0 1 7.4-2" />
    </>
  ));
}

export function IconActivity({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <path d="M2.5 12h4l2.2-7 4.2 14 2.2-9 1.8 4.5h4.6" />
  ));
}

export function IconGlobe({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18M12 3c2.6 2.5 4 5.7 4 9s-1.4 6.5-4 9c-2.6-2.5-4-5.7-4-9s1.4-6.5 4-9z" />
    </>
  ));
}

export function IconRefresh({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <path d="M20 11a8 8 0 0 0-14.6-4.5M4 13a8 8 0 0 0 14.6 4.5" />
      <path d="M20 4v5h-5M4 20v-5h5" />
    </>
  ));
}

export function IconUpload({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <path d="M12 15.5V4.5M8 8.3l4-3.8 4 3.8" />
      <path d="M4.5 15.5v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3" />
    </>
  ));
}

export function IconDownload({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <path d="M12 4.5v11M8 12.2l4 3.8 4-3.8" />
      <path d="M4.5 15.5v3a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-3" />
    </>
  ));
}

export function IconVideo({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <rect x="3.5" y="6.5" width="12" height="11" rx="2" />
      <path d="M15.5 10.2l5-2.7v9l-5-2.7" />
    </>
  ));
}

export function IconMoon({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z" />
  ));
}

export function IconKey({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <circle cx="8" cy="15" r="4" />
      <path d="M11.2 11.8L20 3M16.5 6.5l2.7 2.7M19 4l2 2" />
    </>
  ));
}

export function IconTarget({ size = 18, color = 'currentColor' }) {
  return base(size, color, (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.5" />
      <circle cx="12" cy="12" r="0.6" fill={color} stroke="none" />
    </>
  ));
}