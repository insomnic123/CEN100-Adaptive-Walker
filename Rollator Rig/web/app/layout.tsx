import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "im fucking balding", template: "%s · skibidi dob dob" },
  description: "cen100 is fried",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
