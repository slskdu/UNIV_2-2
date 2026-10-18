// 1~3. 화면을 구성할 3개의 하위(자식) 컴포넌트들을 불러옵니다.
import Header from './Header'
import Content from './Content'
import Footer from './Footer'

// 5. [핵심 포인트] 부모(RootPage)가 넘겨준 Props를 구조 분해 할당({ })으로 받습니다.
// props.dark, props.setDark 라고 쓰기 번거로우므로 바로 분해해서 받았습니다.
const Page = ({dark, setDark}) => {
    return (
        <div>
            {/* 8~9. Header와 Content 컴포넌트에는 '현재 다크모드인지 아닌지'에 대한 상태(dark)만 넘겨줍니다. 
                (화면 색상만 바꾸면 되기 때문입니다.) */}
            <Header dark={dark}/>
            <Content dark={dark}/>
            
            {/* 10. Footer 컴포넌트에는 상태(dark)뿐만 아니라 상태를 변경하는 함수(setDark)도 같이 넘겨줍니다.
                (Footer 안에 다크모드를 켜고 끄는 '버튼'을 만들 예정이기 때문입니다.) */}
            <Footer dark={dark} setDark={setDark}/>
        </div>
    )
}
export default Page