import type { Metadata } from "next";
import SimEditor from "@/components/sim/SimEditor";

export const metadata: Metadata = { title: "sand my box" };

export default function SimPage() {
  return <SimEditor />;
}
