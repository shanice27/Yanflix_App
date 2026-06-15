// app/page.tsx is good to go!
import { redirect } from 'next/navigation';

export default function Home() {
  redirect('/ui/index.html');
}