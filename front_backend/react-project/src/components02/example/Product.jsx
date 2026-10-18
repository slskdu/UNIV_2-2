import React from 'react'

const Product = ({pro}) => {
    const {id,name,price} = pro;
    return (
        <>
            <tr>
                <td>{id}</td>
                <td>{name}</td>
                <td>{parseInt(price).toLocaleString()}</td>
            </tr>
            <tr>
                <td colSpan={3}>상품설명</td>
            </tr>
        </>
    )
}
export default Product