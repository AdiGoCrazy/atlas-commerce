import streamlit as st
from adaptive_commerce.presentation.view_models import (
    validate_candidate_region,
    prepare_region_rows,
    finance_empty_message,
)

st.set_page_config(
    page_title="Atlas Commerce",
    page_icon="",
    layout="wide"
)

regions=[
    {"name":"Bengaluru","status":"Active"},
    {"name":"Mumbai","status":"Active"},
    {"name":"Delhi","status":"Active"}
]

st.title("Atlas Commerce")
st.write("Regional commerce recommendation system")
st.divider()

st.header("1. Existing Regions")
st.table(prepare_region_rows(regions))
st.divider()

st.header("2. Add Candidate Region")
with st.form("candidate_region_form"):
    region_name=st.text_input("Region Name")
    country=st.text_input("Country", value="India")
    submitted=st.form_submit_button("Submit Candidate")

    if submitted:
        error = validate_candidate_region(region_name, country)

        if error:
            st.error(error)
        else:
            st.success("Candidate details validated successfully.")
            st.info("Candidate submission is not connected yet.")

st.divider()

st.header("3. Finance Assessment")

st.info(finance_empty_message(None))

st.divider()

st.header("4. Product Recommendations")

st.warning("Storefront recommendations coming soon.")

st.caption("Atlas Commerce | Sprint 1 Dashboard")

