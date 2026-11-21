import Link from "next/link";

export default function Home() {
  return (
    <main className="home">
      <h1>Rollator accessibility kit</h1>
      <p>Test bench for the walker sensor kit: draw a course, run it on the cardboard rig, see what the sensors detect.</p>
      <ul>
        <li><Link href="/sim">Rig sandbox</Link>: draw a course and send it to the rig</li>
      </ul>
    </main>
  );
}
